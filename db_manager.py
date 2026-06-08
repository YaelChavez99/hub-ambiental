import sqlite3

def inicializar_db():
    # Esto crea el archivo hub_ambiental.db si no existe
    conn = sqlite3.connect('hub_ambiental.db')
    c = conn.cursor()

    # 1. Tabla Core: Proyectos
    c.execute('''
        CREATE TABLE IF NOT EXISTS proyectos (
            id_proyecto TEXT PRIMARY KEY,
            nombre_siniestro TEXT,
            uso_de_suelo TEXT,
            estado TEXT,
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. Tabla Herramienta 1: Fotografías
    c.execute('''
        CREATE TABLE IF NOT EXISTS evidencias_fotograficas (
            id_foto INTEGER PRIMARY KEY AUTOINCREMENT,
            id_proyecto TEXT,
            categoria_ia TEXT,
            pie_de_foto TEXT,
            coordenada_gps TEXT,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto)
        )
    ''')

    # 3. Tabla Herramienta 3: Laboratorio y Coordenadas
    c.execute('''
        CREATE TABLE IF NOT EXISTS datos_laboratorio (
            id_muestra TEXT PRIMARY KEY,
            id_proyecto TEXT,
            zona TEXT,
            coordenadas TEXT,
            json_resultados TEXT,
            rebase_nom BOOLEAN,
            FOREIGN KEY (id_proyecto) REFERENCES proyectos (id_proyecto)
        )
    ''')

    conn.commit()
    conn.close()

# Ejecutamos la función para construir las tablas
inicializar_db()
