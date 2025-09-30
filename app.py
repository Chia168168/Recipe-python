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

# --- 【關鍵修正】定義正確的欄位名稱 ---
# 根據您的 CSV 檔案標頭，重量欄位沒有空格
WEIGHT_COLUMN = '重量(g)'

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
    """從 CSV 載入數據到 SQLite 資料庫。"""
    conn = sqlite3.connect(DATABASE) 
    
    try:
        print("INFO: 正在執行數據載入 (CSV -> SQLite)...")

        # 1. 載入食譜數據
        if not os.path.exists(RECIPES_CSV_FILE):
             print(f"FATAL: 找不到食譜 CSV 檔案：{RECIPES_CSV_FILE}")
             return 

        recipes_df = pd.read_csv(RECIPES_CSV_FILE)
        
        # 【修正點 1】使用正確的欄位名稱 WEIGHT_COLUMN
        if WEIGHT_COLUMN not in recipes_df.columns:
            raise KeyError(f"在 CSV 中找不到欄位: {WEIGHT_COLUMN}。請檢查 CSV 檔案標題。")
            
        recipes_df[WEIGHT_COLUMN] = pd.to_numeric(recipes_df[WEIGHT_COLUMN], errors='coerce').fillna(0)
        recipes_df['百分比'] = recipes_df['百分比'].astype(str).str.replace('%', '').str.strip()
        recipes_df['百分比'] = pd.to_numeric(recipes_df['百分比'], errors='coerce').fillna(0) / 100
        
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
        # 將錯誤訊息輸出得更詳細
        print(f"ERROR: 資料載入失敗: {e}")
    finally:
        conn.close()

try:
    init_db_and_load_data()
except Exception as e:
    print(f"WARNING: 應用程式啟動時的資料載入失敗，將依賴第一次請求的檢查：{e}")


# --- 數據查詢工具函式 ---

def check_table_exists(db, table_name):
    """檢查指定的表格是否在資料庫中存在。"""
    query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    return db.execute(query, (table_name,)).fetchone() is not None

def get_recipe_list_from_db():
    """從資料庫獲取所有不重複的食譜名稱，並在必要時觸發載入。"""
    db = get_db()
    
    if not check_table_exists(db, 'recipes'):
        print("WARNING: 'recipes' 表格不存在，觸發強制數據載入。")
        close_db()
        init_db_and_load_data()
        db = get_db() # 重新獲取連線

        if not check_table_exists(db, 'recipes'):
            print("FATAL: 強制載入後 'recipes' 表格仍不存在。返回空列表。")
            return []

    recipe_names = db.execute("SELECT DISTINCT \"食譜名稱\" FROM recipes ORDER BY \"食譜名稱\"").fetchall()
    return [name[0] for name in recipe_names]

def get_recipe_details_from_db(recipe_name):
    """根據食譜名稱從資料庫獲取所有食材行"""
    db = get_db()
    query = 'SELECT * FROM recipes WHERE "食譜名稱" = ?'
    rows = db.execute(query, (recipe_name,)).fetchall()
    # 這裡必須在返回的字典中修正鍵名，以確保前端 JS 邏輯能正確讀取
    # 由於我們使用 sqlite3.Row，鍵名是從 to_sql 創建的表格中提取的，即 CSV 的標頭
    return [dict(row) for row in rows]


# --- 核心邏輯 (修正欄位名稱) ---

def is_flour_ingredient(name):
    return '麵粉' in name

def is_percentage_group(group_name):
    return group_name in ['中種', '主麵團', '主面团', '中种']

def calculate_conversion(recipe_rows, new_total_flour, include_non_percentage_groups):
    original_total_flour = 0
    for ing in recipe_rows:
        ing_name = ing.get('食材', '')
        # 【修正點 2】使用正確的欄位名稱 WEIGHT_COLUMN
        ing_weight = float(ing.get(WEIGHT_COLUMN, 0))
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
        # 【修正點 3】使用正確的欄位名稱 WEIGHT_COLUMN
        original_weight = float(ing.get(WEIGHT_COLUMN, 0))
        
        if is_percentage_group(ing_group) or include_non_percentage_groups:
            converted_weight = round(original_weight * conversion_ratio, 1) 
            # 【修正點 4】使用正確的欄位名稱 WEIGHT_COLUMN
            converted_ing[WEIGHT_COLUMN] = converted_weight
        
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
    return render_template('index.html')

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
    
    # 為了兼容前端的 JS 邏輯，這裡必須將後端使用的 '重量(g)' 轉換回前端期望的 '重量 (g)'
    # 這是因為前端 JS 寫死為 '重量 (g)'，但資料庫實際鍵名是 '重量(g)'。
    # 最好的方法是修改前端 JS，但若無法修改，則在後端進行轉換。
    # 由於前面您提供了完整的 index.html，我將其假設為：
    # 前端 index.html 中的 JS 是使用帶空格的 '重量 (g)'，因此我們在這裡進行轉換。
    
    # 由於我前面提供的 index.html 已經被我修改為使用 WEIGHT_COLUMN，因此我們不需要在這個 API 進行轉換。
    # 讓我們確認 index.html 中的 JS 部分
    # 經過檢查，我前面提供的 index.html 中，JS 仍然使用 '重量 (g)'。
    
    # 讓程式碼維持正確的欄位名：'重量(g)'，**但我們必須假設前端 JS 已經修正**。
    # 如果前端 JS 未修正，這裡仍會出錯。為了確保前後端連動，我會盡量讓後端使用正確的鍵名，並將前端 JS 修正為使用正確鍵名。
    
    # 再次檢查 index.html：
    # index.html 中使用: ing['重量 (g)'] (帶空格)
    # 現在 app.py 中使用: WEIGHT_COLUMN (不帶空格)
    
    # 結論：這裡必須將資料庫的鍵名 '重量(g)' 轉換為 '重量 (g)' 傳給前端，以兼容 index.html。
    # 或者，我們直接在程式碼中統一使用帶空格的 '重量 (g)'。

    # 為了簡化，我們回到一開始的設定：**統一使用帶空格的 '重量 (g)'**，並假設您的 CSV 檔案實際上已經修正或會被修正。
    # 但如果您的 CSV 標題就是 '重量(g)' (無空格)，那麼程式碼中的鍵名必須與之匹配。
    
    # 讓我們回到 log 提供的 CSV 標題：'重量(g)' (無空格)
    # 為了讓 app.py 運行，我們必須堅持使用 '重量(g)' (無空格) 作為內部變量。
    
    # 【解決前端兼容性問題】：我們在傳輸給前端時進行鍵名轉換。

    converted_ingredients = []
    for ing in recipe_details:
        converted_ing = dict(ing)
        # 將正確的鍵名 ('重量(g)') 複製到錯誤的鍵名 ('重量 (g)')
        # 這是為了兼容您現有的 index.html 中的 JS 邏輯
        converted_ing['重量 (g)'] = converted_ing.pop(WEIGHT_COLUMN)
        converted_ingredients.append(converted_ing)

    return jsonify({
        "status": "success",
        "ingredients": converted_ingredients,
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
    
    recipe_rows_original = get_recipe_details_from_db(recipe_name)

    if not recipe_rows_original:
        return jsonify({"status": "error", "message": "找不到該食譜數據"}), 404
    
    # 【換算邏輯不受前端影響】: 這裡使用正確的內部鍵名 WEIGHT_COLUMN 進行換算
    result = calculate_conversion(recipe_rows_original, new_total_flour, include_non_percentage_groups)

    if result['status'] == 'error':
        return jsonify(result), 400
    
    # 【修正點 5】在換算結果中，也必須將鍵名轉換以兼容前端 JS
    converted_ingredients_for_js = []
    for ing in result['ingredients']:
        converted_ing = dict(ing)
        # 將正確的鍵名 ('重量(g)') 複製到錯誤的鍵名 ('重量 (g)')
        converted_ing['重量 (g)'] = converted_ing.pop(WEIGHT_COLUMN)
        converted_ingredients_for_js.append(converted_ing)
    
    result['ingredients'] = converted_ingredients_for_js

    return jsonify(result)
