import sqlite3
import pandas as pd
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- 檔案路徑設定 ---
DATABASE = 'recipes.db'
RECIPES_CSV_FILE = '食譜資料.xlsx - 食譜.csv'
INGREDIENTS_DB_CSV_FILE = '食譜資料.xlsx - Ingredients.csv'

# --- 資料庫工具函式 ---

def get_db_connection():
    """建立並返回資料庫連線"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # 讓查詢結果以字典形式返回
    return conn

def init_db_and_load_data():
    """初始化資料庫並從 CSV 載入資料"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # --- 1. 建立 recipes 表格並載入食譜數據 ---
        print("正在載入食譜數據...")
        # 由於 CSV 中有中文欄位名，使用 pandas 處理最方便
        recipes_df = pd.read_csv(RECIPES_CSV_FILE)
        
        # 欄位清理：確保重量和百分比是浮點數
        recipes_df['重量 (g)'] = pd.to_numeric(recipes_df['重量 (g)'], errors='coerce').fillna(0)
        # 清理百分比欄位，移除 '%' 並轉換為小數
        recipes_df['百分比'] = recipes_df['百分比'].astype(str).str.replace('%', '').str.strip()
        recipes_df['百分比'] = pd.to_numeric(recipes_df['百分比'], errors='coerce').fillna(0) / 100
        
        recipes_df.to_sql('recipes', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(recipes_df)} 筆食譜紀錄到 'recipes' 表。")

        # --- 2. 建立 ingredients_db 表格並載入食材資料庫數據 ---
        print("正在載入食材資料庫數據...")
        ingredients_df = pd.read_csv(INGREDIENTS_DB_CSV_FILE)
        
        # 欄位清理：確保 hydration 是數字
        ingredients_df['hydration'] = pd.to_numeric(ingredients_df['hydration'], errors='coerce').fillna(0)
        
        ingredients_df.to_sql('ingredients_db', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(ingredients_df)} 筆食材紀錄到 'ingredients_db' 表。")
        
    except FileNotFoundError as e:
        print(f"錯誤：找不到 CSV 檔案 - {e.filename}。請確認檔案已放置在專案根目錄。")
    except Exception as e:
        print(f"資料庫初始化或數據載入失敗: {e}")
    finally:
        conn.close()

# 應用程式啟動時呼叫資料庫初始化
with app.app_context():
    init_db_and_load_data()

# --- 數據查詢工具函式 ---

def get_recipe_list_from_db():
    """從資料庫獲取所有不重複的食譜名稱"""
    conn = get_db_connection()
    # 選擇不重複的食譜名稱，並依名稱排序
    recipe_names = conn.execute("SELECT DISTINCT \"食譜名稱\" FROM recipes ORDER BY \"食譜名稱\"").fetchall()
    conn.close()
    # 將查詢結果轉換為列表 of 字符串
    return [name[0] for name in recipe_names]

def get_recipe_details_from_db(recipe_name):
    """根據食譜名稱從資料庫獲取所有食材行"""
    conn = get_db_connection()
    # 這裡必須使用雙引號來包住中文欄位名稱
    query = 'SELECT * FROM recipes WHERE "食譜名稱" = ?'
    rows = conn.execute(query, (recipe_name,)).fetchall()
    conn.close()
    # 將 sqlite3.Row 對象轉換為普通字典列表
    return [dict(row) for row in rows]

# --- 核心邏輯 (與 GAS 邏輯一致，僅數據來源改變) ---

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
    
    # 1. 計算原始總麵粉量 (只計算百分比分組內的麵粉)
    original_total_flour = 0
    for ing in recipe_rows:
        ing_name = ing.get('食材', '')
        # 確保重量是浮點數，即使資料庫返回的也是浮點數
        ing_weight = float(ing.get('重量 (g)', 0))
        ing_group = ing.get('分組', '')
        
        if is_flour_ingredient(ing_name) and is_percentage_group(ing_group):
            original_total_flour += ing_weight

    if original_total_flour <= 0:
        return { 
            "status": "error", 
            "message": "此食譜沒有麵粉食材或麵粉重量為0" 
        }

    # 2. 計算換算比例
    conversion_ratio = new_total_flour / original_total_flour

    # 3. 換算所有食材重量
    converted_ingredients = []
    for ing in recipe_rows:
        converted_ing = ing.copy()
        ing_group = ing.get('分組', '')
        original_weight = float(ing.get('重量 (g)', 0))
        
        # 只有在百分比分組中的食材才進行換算，或者如果用戶選擇包含非百分比分組
        if is_percentage_group(ing_group) or include_non_percentage_groups:
            # 四捨五入到小數點後一位
            converted_weight = round(original_weight * conversion_ratio, 1) 
            # SQLite 的 Row 對象轉換為 dict 後，key 是字符串，這裡我們需要確保 '重量 (g)' 存儲的是數字
            converted_ing['重量 (g)'] = converted_weight
        
        converted_ingredients.append(converted_ing)

    return {
        "status": "success",
        "originalTotalFlour": original_total_flour,
        "newTotalFlour": new_total_flour,
        "conversionRatio": round(conversion_ratio, 3), # 比例保留三位小數
        "ingredients": converted_ingredients
    }

# --- Flask 路由 ---

@app.route('/')
def index():
    """提供前端 HTML 頁面"""
    return render_template('index.html')

@app.route('/get_recipe_list', methods=['GET'])
def get_recipe_list():
    """提供食譜名稱列表給前端"""
    recipe_names = get_recipe_list_from_db()
    return jsonify(recipe_names)

@app.route('/load_recipe', methods=['POST'])
def load_recipe():
    """根據食譜名稱載入詳細數據 (供前端顯示)"""
    data = request.json
    recipe_name = data.get('recipeName')
    
    if not recipe_name:
        return jsonify({"status": "error", "message": "未提供食譜名稱"}), 400

    recipe_details = get_recipe_details_from_db(recipe_name)
    
    if not recipe_details:
        return jsonify({"status": "error", "message": "找不到該食譜"}), 404

    # 抽取烘烤資訊 (只取第一行的即可)
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
    """處理前端的食材換算請求"""
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
    
    # 1. 取得原始食譜數據
    recipe_rows = get_recipe_details_from_db(recipe_name)

    if not recipe_rows:
        return jsonify({"status": "error", "message": "找不到該食譜數據"}), 404
    
    # 2. 執行換算邏輯
    result = calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups)

    if result['status'] == 'error':
        return jsonify(result), 400
    
    # 3. 成功返回結果
    return jsonify(result)

# --- 啟動設定 ---

if __name__ == '__main__':
    # 這行僅用於本地測試
    app.run(host='0.0.0.0', port=5000, debug=True)
