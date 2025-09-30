import sqlite3
import pandas as pd
from flask import Flask, render_template, request, jsonify, g
import os

app = Flask(__name__)

# --- 檔案路徑設定：使用 os 模組建構絕對路徑 ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 資料庫與 CSV 檔案的路徑
DATABASE = os.path.join(BASE_DIR, 'recipes.db')
RECIPES_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - 食譜.csv')
INGREDIENTS_DB_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - Ingredients.csv')

# --- 資料庫連線管理 ---

def get_db():
    """在每個請求中獲取一個資料庫連線，如果不存在則創建。"""
    if 'db' not in g:
        # check_same_thread=False 適用於 Gunicorn 這種多線程/多進程環境
        g.db = sqlite3.connect(DATABASE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row  # 讓查詢結果以字典形式返回
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """在請求結束時關閉資料庫連線。"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- 資料載入與初始化 ---

def init_db_and_load_data():
    """初始化資料庫並從 CSV 載入資料 (只在應用程式啟動時運行一次)。"""
    # 使用一個獨立的連線來執行初始化，不使用 g.db
    conn = sqlite3.connect(DATABASE)
    
    try:
        print("正在檢查並載入食譜數據...")
        
        # 1. 載入食譜數據
        if not os.path.exists(RECIPES_CSV_FILE):
             raise FileNotFoundError(RECIPES_CSV_FILE)

        recipes_df = pd.read_csv(RECIPES_CSV_FILE)
        recipes_df['重量 (g)'] = pd.to_numeric(recipes_df['重量 (g)'], errors='coerce').fillna(0)
        recipes_df['百分比'] = recipes_df['百分比'].astype(str).str.replace('%', '').str.strip()
        recipes_df['百分比'] = pd.to_numeric(recipes_df['百分比'], errors='coerce').fillna(0) / 100
        
        # 使用 if_exists='replace' 確保每次啟動都重新載入最新的 CSV 數據
        recipes_df.to_sql('recipes', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(recipes_df)} 筆食譜紀錄到 'recipes' 表。")

        # 2. 載入食材資料庫數據
        if not os.path.exists(INGREDIENTS_DB_CSV_FILE):
             raise FileNotFoundError(INGREDIENTS_DB_CSV_FILE)

        ingredients_df = pd.read_csv(INGREDIENTS_DB_CSV_FILE)
        ingredients_df['hydration'] = pd.to_numeric(ingredients_df['hydration'], errors='coerce').fillna(0)
        ingredients_df.to_sql('ingredients_db', conn, if_exists='replace', index=False)
        print(f"成功載入 {len(ingredients_df)} 筆食材紀錄到 'ingredients_db' 表。")
        
    except FileNotFoundError as e:
        print(f"致命錯誤：找不到 CSV 檔案 - {e.filename}。請確認檔案已放置在專案根目錄。")
    except Exception as e:
        print(f"資料庫初始化或數據載入失敗: {e}")
    finally:
        conn.close()

# 在應用程式啟動時運行一次，確保數據在所有工作進程啟動前準備好
# 我們使用 app.before_first_request 或 app.cli.command 來處理 WSGI 環境
# 在 Render/Gunicorn 環境中，最簡單且穩健的方式是直接調用它（因為 Gunicorn 的啟動模式）
# 或者使用 Flask CLI command
try:
    init_db_and_load_data()
except Exception as e:
    print(f"應用程式啟動資料載入失敗: {e}")


# --- 數據查詢工具函式 (改用 get_db() 獲取連線) ---

def get_recipe_list_from_db():
    """從資料庫獲取所有不重複的食譜名稱"""
    db = get_db()
    # 這裡必須使用雙引號來包住中文欄位名稱
    recipe_names = db.execute("SELECT DISTINCT \"食譜名稱\" FROM recipes ORDER BY \"食譜名稱\"").fetchall()
    return [name[0] for name in recipe_names]

def get_recipe_details_from_db(recipe_name):
    """根據食譜名稱從資料庫獲取所有食材行"""
    db = get_db()
    query = 'SELECT * FROM recipes WHERE "食譜名稱" = ?'
    rows = db.execute(query, (recipe_name,)).fetchall()
    return [dict(row) for row in rows]

# --- 核心邏輯 (保持不變) ---

def is_flour_ingredient(name):
    return '麵粉' in name

def is_percentage_group(group_name):
    return group_name in ['中種', '主麵團', '主面团', '中种']

def calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups):
    original_total_flour = 0
    for ing in recipe_rows:
        ing_name = ing.get('食材', '')
        ing_weight = float(ing.get('重量 (g)', 0))
        ing_group = ing.get('分組', '')
        
        if is_flour_ingredient(ing_name) and is_percentage_group(ing_group):
            original_total_flour += ing_weight

    if original_total_flour <= 0:
        return { "status": "error", "message": "此食譜沒有麵粉食材或麵粉重量為0" }

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
    """提供前端 HTML 頁面"""
    return render_template('index.html')

@app.route('/get_recipe_list', methods=['GET'])
def get_recipe_list():
    """提供食譜名稱列表給前端"""
    # 這裡會自動使用 get_db()
    recipe_names = get_recipe_list_from_db()
    return jsonify(recipe_names)

@app.route('/load_recipe', methods=['POST'])
def load_recipe():
    """根據食譜名稱載入詳細數據"""
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
    
    recipe_rows = get_recipe_details_from_db(recipe_name)

    if not recipe_rows:
        return jsonify({"status": "error", "message": "找不到該食譜數據"}), 404
    
    result = calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups)

    if result['status'] == 'error':
        return jsonify(result), 400
    
    return jsonify(result)
