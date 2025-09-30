import os
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, g
import pandas as pd

# 確保在執行程式碼前安裝 Flask 和 pandas
# pip install flask pandas

app = Flask(__name__)

# 資料庫設定
DATABASE = 'recipe_manager.db'

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

def init_db():
    """初始化資料庫結構 (食譜表和食材資料庫表)"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # 1. 食譜主表
        # 欄位依據 code.gs 中的定義: 
        # 食譜名稱, 分組, 食材, 重量 (g), 百分比, 說明, 步驟, 建立時間, 上火溫度, 下火溫度, 烘烤時間, 旋風, 蒸汽
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
        
        # 2. 食材資料庫表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ingredients_db (
                Name TEXT PRIMARY KEY,
                Hydration REAL
            )
        """)
        
        db.commit()

# --- 資料轉換和讀取函數 ---

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
    
def get_all_recipes_data():
    """從資料庫讀取所有食譜資料並整理成前端需要的結構"""
    db = get_db()
    
    # 讀取所有資料
    df = pd.read_sql_query("SELECT * FROM recipes ORDER BY RecipeName, id", db)

    if df.empty:
        return []

    # 按食譜名稱分組
    recipes_grouped = df.groupby('RecipeName')
    
    recipes_list = []

    for name, group in recipes_grouped:
        first_row = group.iloc[0]
        
        # 提取烘烤資訊 (只取第一行的即可)
        baking_info = {
            'topHeat': first_row['UpperTemp'],
            'bottomHeat': first_row['LowerTemp'],
            'time': first_row['BakeTime'],
            'convection': first_row['Convection'] == '是',
            'steam': first_row['Steam'] == '是',
        }
        
        # 整理食材列表
        ingredients = []
        for _, row in group.iterrows():
            ingredients.append({
                'group': row['IngredientGroup'],
                'name': row['IngredientName'],
                'weight': row['Weight_g'],
                'percent': row['Percentage'], # 這裡已經是小數
                'desc': row['Description'],
            })

        # 整理食譜物件
        recipe_obj = {
            'title': name,
            'steps': first_row['Steps'],
            'timestamp': first_row['Timestamp'],
            'baking': baking_info,
            'ingredients': ingredients,
        }
        recipes_list.append(recipe_obj)
        
    return recipes_list

# --- 路由定義 ---

@app.route('/')
def index():
    """主頁面，載入 index.html"""
    return render_template('index.html')

@app.route('/get_recipes', methods=['GET'])
def get_recipes_route():
    """獲取所有食譜的完整數據"""
    recipes = get_all_recipes_data()
    return jsonify(recipes)

@app.route('/get_recipe_list', methods=['GET'])
def get_recipe_list_route():
    """獲取食譜名稱列表 (用於下拉選單)"""
    recipes = get_all_recipes_data()
    recipe_names = sorted([r['title'] for r in recipes])
    return jsonify(recipe_names)

@app.route('/save_recipe', methods=['POST'])
def save_recipe_route():
    """新增或修改食譜 (修改邏輯：先刪除舊的再新增)"""
    data = request.get_json()
    
    title = data.get('title')
    ingredients = data.get('ingredients')
    steps = data.get('steps')
    baking_info = data.get('bakingInfo')
    is_update = data.get('isUpdate', False) # 判斷是否為修改操作
    
    if not title or not ingredients:
        return jsonify({"status": "error", "message": "食譜名稱或食材列表不可為空"}), 400

    db = get_db()
    cursor = db.cursor()
    timestamp = datetime.now().isoformat()
    
    try:
        # 如果是修改操作，先刪除舊的食譜數據
        if is_update:
            cursor.execute("DELETE FROM recipes WHERE RecipeName = ?", (title,))
        
        # 插入新的食譜數據
        for ing in ingredients:
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

@app.route('/delete_recipe', methods=['POST'])
def delete_recipe_route():
    """刪除指定食譜"""
    data = request.get_json()
    title = data.get('recipeName')
    
    if not title:
        return jsonify({"status": "error", "message": "食譜名稱不可為空"}), 400

    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("DELETE FROM recipes WHERE RecipeName = ?", (title,))
        deleted_rows = cursor.rowcount
        db.commit()
        return jsonify({"status": "success", "message": f"食譜 '{title}' 已成功刪除 ({deleted_rows} 行數據)"})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": f"刪除食譜失敗: {str(e)}"}), 500

# --- 食材資料庫路由 ---

@app.route('/get_ingredients_db', methods=['GET'])
def get_ingredients_db_route():
    """獲取自訂食材資料庫列表"""
    db = get_db()
    data = db.execute("SELECT Name, Hydration FROM ingredients_db").fetchall()
    
    # 將結果轉換為前端需要的字典列表 (name, hydration)
    ingredients_db = [{'name': row['Name'], 'hydration': row['Hydration']} for row in data]
    return jsonify(ingredients_db)

@app.route('/save_ingredient_db', methods=['POST'])
def save_ingredient_db_route():
    """新增或修改自訂食材資料庫項目"""
    data = request.get_json()
    name = data.get('name')
    hydration = data.get('hydration')
    
    if not name or hydration is None:
        return jsonify({"status": "error", "message": "食材名稱和含水率不可為空"}), 400
        
    db = get_db()
    cursor = db.cursor()
    
    try:
        # 使用 REPLACE INTO 實現 INSERT OR REPLACE (如果存在則更新)
        cursor.execute("""
            INSERT OR REPLACE INTO ingredients_db (Name, Hydration)
            VALUES (?, ?)
        """, (name, hydration))
        db.commit()
        
        return jsonify({"status": "success", "message": f"已儲存食材：{name}，含水率：{hydration}%"})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": f"儲存食材失敗: {str(e)}"}), 500

@app.route('/delete_ingredient_db', methods=['POST'])
def delete_ingredient_db_route():
    """刪除自訂食材資料庫項目"""
    data = request.get_json()
    name = data.get('name')
    
    if not name:
        return jsonify({"status": "error", "message": "食材名稱不可為空"}), 400

    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("DELETE FROM ingredients_db WHERE Name = ?", (name,))
        db.commit()
        return jsonify({"status": "success", "message": f"已刪除食材：{name}"})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": f"刪除食材失敗: {str(e)}"}), 500

# --- 統計資料路由 ---

def is_flour_ingredient(name):
    """檢查食材名稱是否包含麵粉或相關詞彙"""
    return any(keyword in name for keyword in ['麵粉', '粉'])

@app.route('/get_stats', methods=['GET'])
def get_stats_route():
    """獲取統計資料"""
    recipes = get_all_recipes_data()
    total_recipes = len(recipes)
    total_ingredients = 0
    total_weight = 0
    latest_recipe_name = '-'
    latest_timestamp = datetime.min
    
    for recipe in recipes:
        total_ingredients += len(recipe['ingredients'])
        for ing in recipe['ingredients']:
            total_weight += ing['weight']
        
        try:
            current_timestamp = datetime.fromisoformat(recipe['timestamp'].replace('Z', '+00:00'))
            if current_timestamp > latest_timestamp:
                latest_timestamp = current_timestamp
                latest_recipe_name = recipe['title']
        except Exception:
            pass # 忽略無效的時間戳
            
    avg_weight = round(total_weight / total_recipes, 1) if total_recipes > 0 else 0

    return jsonify({
        "totalRecipes": total_recipes,
        "totalIngredients": total_ingredients,
        "avgWeight": avg_weight,
        "latestRecipe": latest_recipe_name
    })

# --- 智能換算路由 (沿用與優化) ---

@app.route('/calculate_conversion', methods=['POST'])
def calculate_conversion_route():
    data = request.get_json()
    recipe_name = data.get('recipeName')
    new_total_flour = float(data.get('newTotalFlour'))
    include_non_percentage_groups = data.get('includeNonPercentageGroups')

    if not recipe_name or new_total_flour <= 0:
        return jsonify({"status": "error", "message": "食譜名稱或目標麵粉總量無效"}), 400

    # 1. 獲取單一食譜數據 (從所有食譜中找到匹配的)
    all_recipes = get_all_recipes_data()
    recipe = next((r for r in all_recipes if r['title'] == recipe_name), None)

    if not recipe:
        return jsonify({"status": "error", "message": "找不到指定的食譜"}), 404

    # 輔助判斷函數
    def is_percentage_group(group):
        return group in ['中種', '主麵團', '主面团', '中种']

    # 2. 計算原始總麵粉量 (僅限百分比分組的麵粉)
    original_total_flour = 0
    for ing in recipe['ingredients']:
        # 由於 pandas 讀取進來的 Percentage 已經是 float 或 None，無需再次轉換
        if is_flour_ingredient(ing['name']) and is_percentage_group(ing['group']):
            original_total_flour += ing['weight'] or 0

    if original_total_flour <= 0:
        return jsonify({"status": "error", "message": "此食譜沒有用於百分比計算的麵粉食材或麵粉重量為0"}), 400

    # 3. 計算換算比例
    conversion_ratio = new_total_flour / original_total_flour

    # 4. 換算所有食材重量
    converted_ingredients = []
    for ing in recipe['ingredients']:
        converted_ing = ing.copy()
        
        # 只有在百分比分組中的食材才進行換算，或者如果用戶選擇包含非百分比分組
        if is_percentage_group(ing['group']) or include_non_percentage_groups:
            original_weight = ing['weight'] or 0
            # 換算並四捨五入到小數點後一位
            converted_ing['weight'] = round(original_weight * conversion_ratio, 1)
        
        converted_ingredients.append(converted_ing)

    return jsonify({
        "status": "success",
        "originalTotalFlour": original_total_flour,
        "newTotalFlour": new_total_flour,
        "conversionRatio": conversion_ratio,
        "ingredients": converted_ingredients
    })

if __name__ == '__main__':
    # 確保在啟動時初始化資料庫
    init_db()
    app.run(debug=True, port=5000)
