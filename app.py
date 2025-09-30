import sqlite3
import pandas as pd
from flask import Flask, render_template, request, jsonify
import os # <-- 新增：用於處理文件路徑

app = Flask(__name__)

# --- 檔案路徑設定：使用 os 模組建構絕對路徑 ---
# 獲取 app.py 所在的絕對路徑
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 資料庫與 CSV 檔案的路徑
DATABASE = os.path.join(BASE_DIR, 'recipes.db')
RECIPES_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - 食譜.csv')
INGREDIENTS_DB_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - Ingredients.csv')

# --- 資料庫工具函式 ---

def get_db_connection():
    """建立並返回資料庫連線"""
    # check_same_thread=False 適用於多線程/多進程環境 (如 Gunicorn)
    conn = sqlite3.connect(DATABASE, check_same_thread=False) 
    conn.row_factory = sqlite3.Row  # 讓查詢結果以字典形式返回
    return conn

def init_db_and_load_data():
    """初始化資料庫並從 CSV 載入資料"""
    conn = get_db_connection()
    
    try:
        # --- 1. 建立 recipes 表格並載入食譜數據 ---
        print("正在載入食譜數據...")
        # 確保文件存在
        if not os.path.exists(RECIPES_CSV_FILE):
             raise FileNotFoundError(RECIPES_CSV_FILE)

        recipes_df = pd.read_csv(RECIPES_CSV_FILE)
        
        # 欄位清理：確保重量和百分比是浮點數
        recipes_df['重量 (g)'] = pd.to_numeric(recipes_df['重量 (g)'], errors='coerce').fillna(0)
        recipes_df['百分比'] = recipes_df['百分比'].astype(str).str.replace('%', '').str.strip()
        recipes_df['百分比'] = pd.to_numeric(recipes_df['百分比'], errors='coerce').fillna(0) / 100
        
        recipes_df.to_sql('recipes', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(recipes_df)} 筆食譜紀錄到 'recipes' 表。")

        # --- 2. 建立 ingredients_db 表格並載入食材資料庫數據 ---
        print("正在載入食材資料庫數據...")
        if not os.path.exists(INGREDIENTS_DB_CSV_FILE):
             raise FileNotFoundError(INGREDIENTS_DB_CSV_FILE)

        ingredients_df = pd.read_csv(INGREDIENTS_DB_CSV_FILE)
        ingredients_df['hydration'] = pd.to_numeric(ingredients_df['hydration'], errors='coerce').fillna(0)
        
        ingredients_df.to_sql('ingredients_db', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(ingredients_df)} 筆食材紀錄到 'ingredients_db' 表。")
        
    except FileNotFoundError as e:
        # 錯誤訊息更明確地指出找不到哪個檔案
        print(f"錯誤：找不到 CSV 檔案 - {e.filename}。請確認檔案已放置在專案根目錄。")
    except Exception as e:
        print(f"資料庫初始化或數據載入失敗: {e}")
    finally:
        conn.close()

# 應用程式啟動時呼叫資料庫初始化
with app.app_context():
    init_db_and_load_data()

# --- 數據查詢工具函式 (保持不變) ---

def get_recipe_list_from_db():
    """從資料庫獲取所有不重複的食譜名稱"""
    conn = get_db_connection()
    recipe_names = conn.execute("SELECT DISTINCT \"食譜名稱\" FROM recipes ORDER BY \"食譜名稱\"").fetchall()
    conn.close()
    return [name[0] for name in recipe_names]

def get_recipe_details_from_db(recipe_name):
    """根據食譜名稱從資料庫獲取所有食材行"""
    conn = get_db_connection()
    query = 'SELECT * FROM recipes WHERE "食譜名稱" = ?'
    rows = conn.execute(query, (recipe_name,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

# --- 核心邏輯 (保持不變) ---

def is_flour_ingredient(name):
    """判斷是否為麵粉類食材 (可根據需要調整關鍵字)"""
    return '麵粉' in name

def is_percentage_group(group_name):
    """判斷是否為百分比分組 (可根據需要調整關鍵字)"""
    return group_name in ['中種', '主麵團', '主面团', '中种']

def calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups):
    """
    執行食材重量換算的核心邏輯
    """
    original_total_flour = 0
    for ing in recipe_rows:
        ing_name = ing.get('食材', '')
        ing_weight = float(ing.get('重量 (g)', 0))
        ing_group = ing.get('分組', '')
        
        if is_flour_ingredient(ing_name) and is_percentage_group(ing_group):
            original_total_flour += ing_weight

    if original_total_flour <= 0:
        return { 
            "status": "error", 
            "message": "此食譜沒有麵粉食材或麵粉重量為0" 
        }

    conversion_ratio = new_total_flour / original_total_flour

    converted_ingredients = []
    for ing in recipe_rows:
        converted_ing = ing.copy()
        ing_group = ing.get('分組', '')
        original_weight = float(ing.get('重量 (g)', 0))
        
        if is_percentage_group(ing_group) or include_non_percentage_groups:
            converted_weight = round(original_weight * conversion_ratio, 1) 
            converted_ing['重量 (g)'] = converted_weight
        
        converted_ingredients.append(converted_ing)

    return {
        "status": "success",
        "originalTotalFlour": original_total_flour,
        "newTotalFlour": new_total_flour,
        "conversionRatio": round(conversion_ratio, 3), 
        "ingredients": converted_ingredients
    }

# --- Flask 路由 ---

@app.route('/')
def index():
    """提供前端 HTML 頁面 (Flask 將在 'templates' 資料夾中尋找 index.html)"""
    return render_template('index.html')

# ... (其他路由保持不變) ...

@app.route('/get_recipe_list', methods=['GET'])
def get_recipe_list():
    recipe_names = get_recipe_list_from_db()
    return jsonify(recipe_names)

@app.route('/load_recipe', methods=['POST'])
def load_recipe():
    data = request.json
    recipe_name = data.get('recipeName')
    
    if not recipe_name:
        return jsonify({"status": "error", "message": "未提供食譜名稱"}), 400

    recipe_details = get_recipe_details_from_db(recipe_name)
    
    if not recipe_details:
        return jsonify({"status": "error", "message": "找不到該食譜"}), 404

    first_row = recipe_details[0]
    baking_info = {
        'upperTemp': first_row.get('上火溫度'),
        'lowerTemp': first_row.get('下火溫度'),
        'bakeTime': first_row.get('烘烤時間'),
        'convection': first_row.get('旋風'),
        'steam': first_row.get('蒸汽')
    }
    
    return jsonify({
        "status": "success",
        "ingredients": recipe_details,
        "bakingInfo": baking_info
    })


@app.route('/calculate_conversion', methods=['POST'])
def handle_calculate_conversion():
    data = request.json
    
    recipe_name = data.get('recipeName')
    new_total_flour = data.get('newTotalFlour')
    include_non_percentage_groups = data.get('includeNonPercentageGroups', False)

    if not recipe_name or new_total_flour is None:
        return jsonify({"status": "error", "message": "參數不完整"}), 400
    
    try:
        new_total_flour = float(new_total_flour)
    except ValueError:
        return jsonify({"status": "error", "message": "新的總麵粉量必須是數字"}), 400
    
    recipe_rows = get_recipe_details_from_db(recipe_name)

    if not recipe_rows:
        return jsonify({"status": "error", "message": "找不到該食譜數據"}), 404
    
    result = calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups)

    if result['status'] == 'error':
        return jsonify(result), 400
    
    return jsonify(result)

# --- 啟動設定 ---
# 註釋掉 if __name__ == '__main__': 區塊，因為 Render 會使用 Gunicorn 啟動
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, debug=True)
