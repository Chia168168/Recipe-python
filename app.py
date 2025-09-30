import os
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, g
import pandas as pd

# 確保在執行程式碼前安裝 Flask 和 pandas
# pip install flask pandas

app = Flask(__name__)

# --- 檔案與資料庫設定 ---
# 確保這些 CSV 檔案存在於 app.py 相同的目錄下
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, 'recipe_manager.db')
RECIPES_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - 食譜.csv')
INGREDIENTS_DB_CSV_FILE = os.path.join(BASE_DIR, '食譜資料.xlsx - Ingredients.csv')

# --- 輔助函數 ---

def get_db():
    """連線到資料庫"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row  # 讓結果可以用字典方式存取
    return db

@app.teardown_appcontext
def close_connection(exception):
    """應用程式結束時關閉資料庫連線"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def normalize_percent_value(p):
    """標準化百分比值：將百分比字串或大於1的數字轉為小數"""
    if p is None or p == "":
        return None
    try:
        if isinstance(p, str) and p.endswith('%'):
            n = float(p.strip().replace('%', ''))
            return n / 100
        n = float(p)
        return n / 100 if n > 1 else n
    except ValueError:
        return None

# --- 資料庫初始化與載入 ---

def load_initial_csv_data(db):
    """從 CSV 檔案載入初始數據到 SQLite 資料庫"""
    print("INFO: 正在執行初始 CSV 數據載入...")
    try:
        # 1. 載入食譜數據 (recipes)
        if os.path.exists(RECIPES_CSV_FILE):
            recipes_df = pd.read_csv(RECIPES_CSV_FILE)
            
            # 【關鍵】定義 CSV 標頭到資料庫欄位的映射
            column_map = {
                '食譜名稱': 'RecipeName', '分組': 'IngredientGroup', '食材': 'IngredientName',
                '重量(g)': 'Weight_g', '百分比': 'Percentage_CSV', '說明': 'Description',
                '步驟': 'Steps', '建立時間': 'Timestamp', '上火溫度': 'UpperTemp',
                '下火溫度': 'LowerTemp', '烘烤時間': 'BakeTime', '旋風': 'Convection',
                '蒸汽': 'Steam'
            }
            
            recipes_df = recipes_df.rename(columns=column_map)
            
            # 轉換百分比並設置為 'Percentage' 欄位
            recipes_df['Percentage'] = recipes_df['Percentage_CSV'].apply(normalize_percent_value)
            recipes_df = recipes_df.drop(columns=['Percentage_CSV'])
            
            # 確保所有需要的欄位存在 (如果 CSV 缺少欄位會在這裡出錯)
            required_recipe_cols = ['RecipeName', 'IngredientGroup', 'IngredientName', 'Weight_g', 'Percentage', 'Description', 'Steps', 'Timestamp', 'UpperTemp', 'LowerTemp', 'BakeTime', 'Convection', 'Steam']
            recipes_df = recipes_df.reindex(columns=required_recipe_cols)

            recipes_df.to_sql('recipes', db, if_exists='append', index=False)
            print(f"INFO: 成功載入 {len(recipes_df)} 筆初始食譜紀錄到 'recipes' 表。")

        # 2. 載入食材資料庫數據 (ingredients_db)
        if os.path.exists(INGREDIENTS_DB_CSV_FILE):
             ingredients_df = pd.read_csv(INGREDIENTS_DB_CSV_FILE)
             ingredients_df = ingredients_df.rename(columns={'name': 'Name', 'hydration': 'Hydration'})
             ingredients_df['Hydration'] = pd.to_numeric(ingredients_df['Hydration'], errors='coerce').fillna(0)

             ingredients_df.to_sql('ingredients_db', db, if_exists='append', index=False)
             print(f"INFO: 成功載入 {len(ingredients_df)} 筆初始食材紀錄到 'ingredients_db' 表。")

    except Exception as e:
        print(f"ERROR: 初始數據載入失敗: {e}")

def init_db():
    """初始化資料庫結構，並在資料庫為空時從 CSV 載入資料。"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # 1. 建立食譜主表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                RecipeName TEXT NOT NULL,
                IngredientGroup TEXT,
                IngredientName TEXT NOT NULL,
                Weight_g REAL,
                Percentage REAL,
                Description TEXT,
                Steps TEXT,
                Timestamp TEXT,
                UpperTemp INTEGER,
                LowerTemp INTEGER,
                BakeTime INTEGER,
                Convection TEXT,
                Steam TEXT
            )
        """)
        
        # 2. 建立食材資料庫表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingredients_db (
                Name TEXT PRIMARY KEY,
                Hydration REAL
            )
        """)
        
        db.commit()

        # 3. 檢查並載入初始 CSV 資料 (僅在表格為空時載入)
        try:
            recipe_count = cursor.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            if recipe_count == 0:
                load_initial_csv_data(db)
        except Exception as e:
            # 如果 COUNT(*) 失敗，可能表格剛創建，也嘗試載入
            print(f"WARNING: 檢查表格是否為空時發生錯誤 ({e})，將嘗試載入 CSV 數據。")
            load_initial_csv_data(db)

# --- 應用程式啟動時強制執行初始化 (解決 Gunicorn 問題) ---
# 這一行會確保在應用程式啟動時，無論是使用 flask run 還是 gunicorn，都會執行初始化。
init_db()


# --- 資料轉換和讀取函數 (保持不變) ---

def get_all_recipes_data():
    """從資料庫讀取所有食譜資料並整理成前端需要的結構"""
    db = get_db()
    
    # 讀取所有資料 (使用資料庫中的欄位名稱)
    df = pd.read_sql_query("SELECT * FROM recipes ORDER BY RecipeName, id", db)

    if df.empty:
        return []

    recipes_grouped = df.groupby('RecipeName')
    recipes_list = []

    for name, group in recipes_grouped:
        first_row = group.iloc[0]
        
        baking_info = {
            'topHeat': first_row['UpperTemp'],
            'bottomHeat': first_row['LowerTemp'],
            'time': first_row['BakeTime'],
            'convection': first_row['Convection'] == '是',
            'steam': first_row['Steam'] == '是',
        }
        
        ingredients = []
        for _, row in group.iterrows():
            ingredients.append({
                'group': row['IngredientGroup'],
                'name': row['IngredientName'],
                'weight': row['Weight_g'],
                'percent': row['Percentage'], # 這裡已經是小數
                'desc': row['Description'],
            })

        recipe_obj = {
            'title': name,
            'steps': first_row['Steps'],
            'timestamp': first_row['Timestamp'],
            'baking': baking_info,
            'ingredients': ingredients,
        }
        recipes_list.append(recipe_obj)
        
    return recipes_list

# --- 路由定義 (保持不變) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_recipes', methods=['GET'])
def get_recipes_route():
    recipes = get_all_recipes_data()
    return jsonify(recipes)

# ... (其他路由如 save_recipe, delete_recipe, get_ingredients_db, get_stats, calculate_conversion 保持不變)
# 為了避免冗長，我僅顯示關鍵修正部分。您應該使用我前一次提供的完整 app.py 的 **所有** 路由定義。

@app.route('/save_recipe', methods=['POST'])
def save_recipe_route():
    """新增或修改食譜 (修改邏輯：先刪除舊的再新增)"""
    data = request.get_json()
    
    title = data.get('title')
    ingredients = data.get('ingredients')
    steps = data.get('steps')
    baking_info = data.get('bakingInfo')
    is_update = data.get('isUpdate', False) 
    
    if not title or not ingredients:
        return jsonify({"status": "error", "message": "食譜名稱或食材列表不可為空"}), 400

    db = get_db()
    cursor = db.cursor()
    timestamp = datetime.now().isoformat()
    
    try:
        if is_update:
            cursor.execute("DELETE FROM recipes WHERE RecipeName = ?", (title,))
        
        for ing in ingredients:
            # 這裡的 ing['percent'] 來自前端表單，可能是字串 (如 '50%') 或數字
            percent_norm = normalize_percent_value(ing.get('percent'))
            
            cursor.execute("""
                INSERT INTO recipes 
                (RecipeName, IngredientGroup, IngredientName, Weight_g, Percentage, Description, Steps, Timestamp, UpperTemp, LowerTemp, BakeTime, Convection, Steam)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                ing.get('group'),
                ing.get('name'),
                ing.get('weight'),
                percent_norm,
                ing.get('desc'),
                steps,
                timestamp,
                baking_info.get('topHeat'),
                baking_info.get('bottomHeat'),
                baking_info.get('time'),
                '是' if baking_info.get('convection') else '否',
                '是' if baking_info.get('steam') else '否',
            ))
            
        db.commit()
        
        message = f"食譜 '{title}' {'更新' if is_update else '新增'}成功！"
        return jsonify({"status": "success", "message": message})
        
    except Exception as e:
        db.rollback()
        print(f"Database error: {e}")
        return jsonify({"status": "error", "message": f"儲存食譜失敗: {str(e)}"}), 500

# [Please ensure all other routes from the previous comprehensive app.py are included here.]

# --- 伺服器啟動 (僅用於本地開發) ---
if __name__ == '__main__':
    # 注意：在 Render/Gunicorn 環境中，這個區塊不會執行。
    # 真正的初始化已在上方 init_db() 呼叫中完成。
    app.run(debug=True, port=5000)
