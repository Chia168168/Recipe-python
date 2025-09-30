import sqlite3
import pandas as pd
from flask import Flask, render_template, request, jsonify, g
import os

app = Flask(__name__)

# --- 檔案路徑設定 ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DATABASE = os.path.join(BASE_DIR, 'recipes.db')
RECIPES_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - 食譜.csv')
INGREDIENTS_DB_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - Ingredients.csv')

# --- 資料庫連線管理 ---

def get_db():
    """在每個請求中獲取一個資料庫連線，如果不存在則創建。"""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """在請求結束時關閉資料庫連線。"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- 資料載入與初始化函式 ---

def init_db_and_load_data():
    """
    從 CSV 載入數據到 SQLite 資料庫。
    此函式應在需要確保數據存在時呼叫。
    """
    # 這裡使用一個單獨的連線來執行寫入操作
    conn = sqlite3.connect(DATABASE) 
    
    try:
        print("INFO: 正在執行數據載入 (CSV -> SQLite)...")

        # 1. 載入食譜數據
        if not os.path.exists(RECIPES_CSV_FILE):
             print(f"FATAL: 找不到食譜 CSV 檔案：{RECIPES_CSV_FILE}")
             return 

        recipes_df = pd.read_csv(RECIPES_CSV_FILE)
        recipes_df['重量 (g)'] = pd.to_numeric(recipes_df['重量 (g)'], errors='coerce').fillna(0)
        recipes_df['百分比'] = recipes_df['百分比'].astype(str).str.replace('%', '').str.strip()
        recipes_df['百分比'] = pd.to_numeric(recipes_df['百分比'], errors='coerce').fillna(0) / 100
        
        # 使用 if_exists='replace' 確保每次都建立新表
        recipes_df.to_sql('recipes', conn, if_exists='replace', index=False)
        print(f"INFO: 成功載入 {len(recipes_df)} 筆食譜紀錄到 'recipes' 表。")

        # 2. 載入食材資料庫數據
        if not os.path.exists(INGREDIENTS_DB_CSV_FILE):
             print(f"FATAL: 找不到食材 CSV 檔案：{INGREDIENTS_DB_CSV_FILE}")
             return
        
        ingredients_df = pd.read_csv(INGREDIENTS_DB_CSV_FILE)
        ingredients_df['hydration'] = pd.to_numeric(ingredients_df['hydration'], errors='coerce').fillna(0)
        ingredients_df.to_sql('ingredients_db', conn, if_exists='replace', index=False)
        print(f"INFO: 成功載入 {len(ingredients_df)} 筆食材紀錄到 'ingredients_db' 表。")
        
    except Exception as e:
        print(f"ERROR: 資料載入失敗: {e}")
    finally:
        conn.close()

# 為了確保在 Gunicorn 工作進程啟動時有數據，我們讓它執行一次。
# 實際請求時，會再進行一次檢查 (見 get_recipe_list_from_db)
# 這樣設計可以在大多數情況下讓啟動更流暢。
try:
    init_db_and_load_data()
except Exception as e:
    print(f"WARNING: 應用程式啟動時的資料載入失敗，將依賴第一次請求的檢查：{e}")


# --- 數據查詢工具函式 (新增表格檢查) ---

def check_table_exists(db, table_name):
    """檢查指定的表格是否在資料庫中存在。"""
    query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    return db.execute(query, (table_name,)).fetchone() is not None

def get_recipe_list_from_db():
    """從資料庫獲取所有不重複的食譜名稱，並在必要時觸發載入。"""
    db = get_db()
    
    # 【關鍵修正】檢查表格是否存在。若不存在，強制執行初始化。
    if not check_table_exists(db, 'recipes'):
        print("WARNING: 'recipes' 表格不存在，觸發強制數據載入。")
        # 關閉當前工作進程的連線，執行初始化，然後重新連線
        close_db()
        init_db_and_load_data()
        db = get_db() # 重新獲取連線

        # 在重新嘗試查詢前，再次檢查表格是否存在 (以防 CSV 檔案丟失)
        if not check_table_exists(db, 'recipes'):
            print("FATAL: 強制載入後 'recipes' 表格仍不存在。返回空列表。")
            return []

    # 執行查詢
    recipe_names = db.execute("SELECT DISTINCT \"食譜名稱\" FROM recipes ORDER BY \"食譜名稱\"").fetchall()
    return [name[0] for name in recipe_names]

def get_recipe_details_from_db(recipe_name):
    """根據食譜名稱從資料庫獲取所有食材行"""
    db = get_db()
    # 這裡可以假設如果 get_recipe_list_from_db 成功，recipes 表格就存在
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

# --- Flask 路由 (保持不變) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_recipe_list', methods=['GET'])
def get_recipe_list():
    recipe_names = get_recipe_list_from_db()
    # 如果 data_init 失敗，前端會收到空列表 []
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
