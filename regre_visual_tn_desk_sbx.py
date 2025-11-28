import os
import cv2
import numpy as np
import time
import datetime
import re
import io
import sys 
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# === CONFIGURACIÓN ===
output_dir = "Reportes HTML - TN - DESKTOP - SBX" 
os.makedirs(output_dir, exist_ok=True)

# Umbral de tolerancia en píxeles. (Ej: 2px de diferencia es aceptable, más es falla)
# Se usa para la posición X, Y y la dimensión W, H.
UMBRAL_PIXELES_TOLERANCIA = 0 

# Lista de IDs/Clases de contenedores de anuncios para neutralizar (OCULTAR).
AD_CONTAINER_IDS = [
    'ad-slot-header', 'parent-ad-slot-header', 'parent-ad-slot-caja', 
    'ad-slot-caja1', 'parent-ad-slot-caja2', 'ad-slot-caja3', 
    'google_ads_iframe_', # Prefijo de ID de iframe de Google
    'dfp-ad', 
    'ad-slot-megalateral',
    'cont-sidebar-ad', 
    'aniBox', 
    'banner-container',
]

# ---
## Funciones de Utilidad
# ---

def format_time(seconds):
    """Convierte segundos totales a formato HH:MM:SS."""
    try:
        seconds = int(seconds)
        return str(datetime.timedelta(seconds=seconds))
    except (ValueError, TypeError):
        return "00:00:00"

def format_date(timestamp):
    """Convierte un timestamp (YYYYMMDD_HHMMSS) a formato DD/MM/AAAA."""
    try:
        # CORRECCIÓN: Se ajusta el formato de entrada de "%Y-%m-%d" a "%Y%m%d"
        dt_object = datetime.datetime.strptime(timestamp.split('_')[0], "%Y%m%d")
        return dt_object.strftime("%d/%m/%Y")
    except ValueError:
        return timestamp.split('_')[0]

def ejecutar_js_manipulacion(driver, script):
    """Ejecuta un script JavaScript, ignorando errores."""
    try:
        driver.execute_script(script)
    except Exception:
        pass

# ---
## Función CRÍTICA de Limpieza Estructural
# ---

def limpiar_entorno_robusto(driver):
    """
    Realiza la ELIMINACIÓN de popups flotantes (cookies, notificaciones, suscripciones) 
    pero MANTIENE visible el contenido de ADS para medir su impacto estructural.
    """
    print("    🧹 Eliminación de Popups Flotantes (Si existen)...")

    # 1. ELIMINACIÓN FORZADA DE POPUPS Y ELEMENTOS FIJOS
    js_eliminar_popups = """
        // Intenta hacer click y eliminar
        var btn_close = document.querySelector('button.onetrust-close-btn-handler'); if (btn_close) { btn_close.click(); }
        var os_cancel = document.getElementById('onesignal-slidedown-cancel-button'); if (os_cancel) { os_cancel.click(); }

        // BLINDAJE 1: Eliminación por ID/Clase de Popups Comunes
        var os_container = document.getElementById('onesignal-slidedown-container'); if (os_container) { os_container.remove(); }
        var alert_news = document.getElementById('alertNews'); if (alert_news) { alert_news.remove(); }
        var cookie_modal = document.getElementById('onetrust-consent-sdk'); if (cookie_modal) { cookie_modal.remove(); }
        
        // BLINDAJE 2: Elimina modales de suscripción comunes
        var subscribe_modal_content = document.querySelector('.modal-content-subscribe'); 
        if (subscribe_modal_content) { 
            subscribe_modal_content.remove(); 
        }
        var modal_overlay = document.querySelector('.modal-backdrop');
        if (modal_overlay) { modal_overlay.remove(); }
        
        // BLINDAJE 3: Forzar el ocultamiento de cualquier elemento con z-index alto (Popups flotantes)
        var high_z_index_items = document.querySelectorAll('*[style*="z-index"]:not(body):not(html)');
        high_z_index_items.forEach(function(el) {
            var style = window.getComputedStyle(el);
            var zIndex = style.zIndex;
            // Ocultar si tiene un z-index muy alto (asumiendo que es un popup)
            if (zIndex > 1000 || el.classList.contains('popup')) {
                el.style.display = 'none'; 
            }
        });
        
        // Asegura que el contenedor de la página no se mueva horizontalmente por la remoción de elementos
        document.body.style.overflowX = 'hidden'; 
        document.body.style.maxWidth = '100vw'; 
    """
    ejecutar_js_manipulacion(driver, js_eliminar_popups)
    
def forzar_carga_contenido(driver):
    """
    Ejecuta scrolls suaves para forzar la carga de lazy loading y estabilizar el DOM.
    """
    # 1. Scroll al final
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);") 
    time.sleep(8) 
    # 2. Scroll al inicio
    driver.execute_script("window.scrollTo(0, 0);") 
    time.sleep(8) 
    # 3. Scroll a la mitad para forzar carga central
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);") 
    time.sleep(8)
    # 4. Volver al inicio antes de medir
    driver.execute_script("window.scrollTo(0, 0);") 
    time.sleep(12) 

# ---
## Función Clave: Extracción de Datos del DOM (4 Puntos: X, Y, W, H)
# ---

def obtener_estructura_dom(driver):
    
    """
    Ejecuta JavaScript para obtener el selector CSS, la posición (X, Y) y la dimensión (W, H) de CADA DIV.
    """
    js_script_css_selector = """
        function getCssSelector(el) {
            if (!(el instanceof Element)) return;
            var path = [];
            while (el.nodeType === Node.ELEMENT_NODE) {
                var selector = el.tagName.toLowerCase();
                if (el.id) {
                    selector += '#' + el.id;
                    path.unshift(selector);
                    break;
                } else {
                    var sib = el, nth = 1;
                    while (sib = sib.previousElementSibling) {
                        if (sib.tagName.toLowerCase() == selector) nth++;
                    }
                    if (nth != 1) selector += ":nth-child(" + nth + ")";
                }
                path.unshift(selector);
                el = el.parentNode;
            }
            return path.join(' > ');
        }

        var elements = document.querySelectorAll('div');
        var data = [];
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            var rect = el.getBoundingClientRect();
            
            // Filtra elementos muy pequeños o invisibles
            if (rect.height < 5 || rect.width < 5 || rect.height === 0 || rect.width === 0) continue;
            
            // FILTRAR 'fusion-app', 'common-layout', 'col-megalateral', 'col-content', 'default-article-color'
            if (el.classList && (
    el.classList.contains('fusion-app') ||
    el.classList.contains('common-layout') ||
    el.classList.contains('col-megalateral') ||
    el.classList.contains('default-article-color') ||
    el.classList.contains('col-content')
)) continue;
            

            data.push({
                selector: getCssSelector(el),
                // --- Extracción de ID y Clase ---
                id_attr: el.id, 
                class_attr: el.className, 
                // ---------------------------------
                y: window.pageYOffset + rect.top,       // Posición Vertical ABSOLUTA
                height: rect.height,                     // Altura (Dimensión Vertical)
                x: window.pageXOffset + rect.left,       // Posición Horizontal ABSOLUTA
                width: rect.width                      // Ancho (Dimensión Horizontal)
            });
        }
        return data;
    """
    
    data = []
    png = None
    
    try:
        driver.get(driver.current_url) 
        # Espera de 20 segundos para document.readyState
        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
        
        # === USO DE LA LIMPIZA ROBUSTA ===
        limpiar_entorno_robusto(driver)
        # TIEMPO DE ESPERA ADICIONAL AÑADIDO
        time.sleep(10) 
        limpiar_entorno_robusto(driver) 
        forzar_carga_contenido(driver) 
        # =================================

        print("     📐 Extrayendo posiciones y dimensiones del DOM (X, Y, W, H)...")
        data = driver.execute_script(js_script_css_selector)
        
        # Tomar captura de pantalla 
        print("     📸 Tomando captura de pantalla para el reporte...")
        total_height = driver.execute_script("return Math.max( document.body.scrollHeight, document.body.offsetHeight, document.documentElement.clientHeight, document.documentElement.scrollHeight, document.documentElement.offsetHeight );")
        original_size = driver.get_window_size()
        driver.set_window_size(original_size['width'], total_height)
        png = driver.get_screenshot_as_png()
        driver.set_window_size(original_size['width'], original_size['height'])

    except Exception as e:
        print(f"     ❌ Error en la extracción/captura: {e}")
        data = [{'selector': 'FATAL ERROR', 'y': 0, 'height': 0, 'x': 0, 'width': 0}] 
        
    return data, png


# ---
## Función Clave: Comparación Estructural DOM (Agrupación de Errores)
# ---

def comparar_estructura_dom(data_v1, data_v2, umbral_pixeles):
    """
    Compara la estructura de los DIVs usando sus 4 puntos (X, Y, W, H).
    Agrupa todas las fallas de un selector CSS en una sola entrada.
    """
    v2_map = {item['selector']: item for item in data_v2 if item['selector'] is not None}
    
    # Diccionario para agrupar fallas por selector
    errores_agrupados = {}
    
    # Función de ayuda para añadir una falla a la agrupación
    def add_falla(selector, tipo, diff, v1, v2, coords_v2):
        # Asegurarse de que los valores sean números antes de formatear
        v1_val = v1 if isinstance(v1, (int, float)) else 0
        v2_val = v2 if isinstance(v2, (int, float)) else 0
        
        # Inicialización de la entrada (gravedad inicial: 'menor')
        if selector not in errores_agrupados:
            errores_agrupados[selector] = {
                'selector': selector,
                'tipos': [],
                'coords_v2': coords_v2,
                'cambio_dimension': 0, # Nuevo contador
                'cambio_posicion': 0,  # Nuevo contador
                'gravedad': 'menor' 
            }
        
        # Añadir el detalle de la falla (con formato condicional para N/A)
        v1_display = f"{v1:.2f}" if isinstance(v1, (int, float)) else str(v1)
        v2_display = f"{v2:.2f}" if isinstance(v2, (int, float)) else str(v2)
        diff_display = f"{diff:.2f}px" if isinstance(diff, (int, float)) else str(diff)

        detalle = f"Tipo: <b>{tipo}</b> | V1: {v1_display} | V2: {v2_display} | Diff: {diff_display}"
        errores_agrupados[selector]['tipos'].append(detalle)
        
        # Actualización de contadores de cambios
        if 'ALTURA (H)' in tipo or 'ANCHO (W)' in tipo:
             errores_agrupados[selector]['cambio_dimension'] += 1
        elif 'POSICIÓN (Y)' in tipo or 'POSICIÓN (X)' in tipo:
             errores_agrupados[selector]['cambio_posicion'] += 1
        
        # Fallas de existencia son siempre graves
        if tipo in ['AUSENTE V2', 'NUEVO EN V2']:
            errores_agrupados[selector]['gravedad'] = 'grave'


    for item1 in data_v1:
        selector = item1['selector']
        
        if selector in v2_map and selector is not None:
            item2 = v2_map[selector]
            
            # 1. ALTURA (H)
            diff_height = abs(item1['height'] - item2['height'])
            if diff_height > umbral_pixeles:
                add_falla(selector, 'DIFERENCIA ALTURA (H)', diff_height, item1['height'], item2['height'], item2)

            # 2. POSICIÓN Y
            diff_y = abs(item1['y'] - item2['y'])
            if diff_y > umbral_pixeles:
                 add_falla(selector, 'DIFERENCIA POSICIÓN (Y)', diff_y, item1['y'], item2['y'], item2)
            
            # 3. ANCHO (W)
            diff_width = abs(item1['width'] - item2['width'])
            if diff_width > umbral_pixeles:
                add_falla(selector, 'DIFERENCIA ANCHO (W)', diff_width, item1['width'], item2['width'], item2)
            
            # 4. POSICIÓN X
            diff_x = abs(item1['x'] - item2['x'])
            if diff_x > umbral_pixeles:
                 add_falla(selector, 'DIFERENCIA POSICIÓN (X)', diff_x, item1['x'], item2['x'], item2)
        
        elif selector not in ['ERROR', 'FATAL ERROR'] and selector is not None:
            # 5. Elemento presente en V1, ausente en V2 (FALLA GRAVE)
             coords_v1_for_mark = {'x': item1['x'], 'y': item1['y'], 'width': item1['width'], 'height': item1['height']} 
             add_falla(selector, 'AUSENTE V2', "N/A", "N/A", "N/A", coords_v1_for_mark)
            
    # 6. Elementos en V2 que no están en V1 (FALLA GRAVE)
    v1_selectors = set(item['selector'] for item in data_v1 if item['selector'] is not None)
    for item2 in data_v2:
        selector = item2['selector']
        if selector not in v1_selectors and selector not in ['ERROR', 'FATAL ERROR'] and selector is not None:
            coords_v2_for_mark = {'x': item2['x'], 'y': item2['y'], 'width': item2['width'], 'height': item2['height']} 
            add_falla(selector, 'NUEVO EN V2', "N/A", "N/A", "N/A", coords_v2_for_mark)

    # 7. CONSOLIDACIÓN FINAL Y CLASIFICACIÓN DE GRAVEDAD
    fallas_final = []
    selectores_fallidos = []
    
    for selector, data in errores_agrupados.items():
        
        # *** LÓGICA DE CLASIFICACIÓN REFINADA ***
        # Si ya se marcó como 'grave' (p. ej. por Ausente/Nuevo) O si cambió W o H, ES GRAVE.
        if data['gravedad'] == 'grave' or data['cambio_dimension'] > 0:
            data['gravedad'] = 'grave'
        # Si NO cambió W ni H, pero sí cambió X y/o Y, es MENOR (Efecto Dominó).
        elif data['cambio_posicion'] > 0:
            data['gravedad'] = 'menor'
        else:
             # Si no hay cambios significativos, se podría omitir, 
             # pero por seguridad, mantenemos el menor si entró en el bucle
             data['gravedad'] = 'menor'
        # *************************************************

        descripcion_consolidada = "<div style='margin-top: 5px; border-left: 2px solid #ccc; padding-left: 5px;'>"+ "<br>".join(data['tipos']) + "</div>"
        
        # Usar el resultado de la lógica refinada
        tipo_marcado = 'DIFERENCIA AGRUPADA GRAVE' if data['gravedad'] == 'grave' else 'DIFERENCIA AGRUPADA MENOR'
        
        fallas_final.append({
            'selector': selector,
            'tipo': tipo_marcado, 
            'diff': 1, 
            'v1': "Consolidado", 
            'v2': descripcion_consolidada, 
            'coords_v2': data['coords_v2']
        })
        selectores_fallidos.append(selector)

    return fallas_final, selectores_fallidos


# ---
## Función para Marcado Visual (OpenCV)
# ---

def marcar_fallas_en_captura(png_data, fallas, data_v2): 
    """
    Toma el PNG de V2 y dibuja un rectángulo ROJO (diferencia grave) o AZUL (diferencia menor)
    sobre cada elemento que falló la prueba DOM.
    """
    if not png_data or not fallas:
        return None 
        
    img_np = np.frombuffer(png_data, np.uint8)
    img = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
    
    selectores_ya_marcados = set()
    
    for f in fallas:
        selector = f.get('selector')
        tipo = f.get('tipo') 
        
        item_coords = f.get('coords_v2') 
        
        if selector in selectores_ya_marcados or item_coords is None:
            continue
            
        # 1. Determinar el color del rectángulo (BGR: OpenCV usa BGR)
        if 'GRAVE' in tipo:
            color_bgr = (0, 0, 255) # ROJO
            thickness = 5
        elif 'MENOR' in tipo:
            color_bgr = (255, 0, 0) # AZUL
            thickness = 3
        else:
            continue 

        # 2. Coordenadas
        x1 = int(item_coords['x'])
        y1 = int(item_coords['y'])
        x2 = int(item_coords['x'] + item_coords['width'])
        y2 = int(item_coords['y'] + item_coords['height'])
        
        # 3. Dibujar
        height, width, _ = img.shape
        # Aplicar límites (Clip)
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(width - 1, x2)
        y2 = min(height - 1, y2)
        
        if x2 > x1 and y2 > y1:
            cv2.rectangle(img, (x1, y1), (x2, y2), color_bgr, thickness) 
            selectores_ya_marcados.add(selector) 
        else:
            # Eliminado el aviso de 'coordenadas inválidas'.
            pass

    is_success, buffer = cv2.imencode(".png", img)
    if is_success:
        return buffer.tobytes()
    return None

# ---
## Función para Inicializar y Cerrar Selenium
# ---

def ejecutar_selenium_para_estructura(url):
    """Maneja la inicialización del driver, llama a la extracción y lo cierra."""
    
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new") 
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu") 
    options.add_argument("--log-level=3") 
    options.add_experimental_option('excludeSwitches', ['enable-logging']) 
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = None
    data = []
    png = None
    
    try:
        os.environ['WDM_LOG_LEVEL'] = '0' 
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60) 
        driver.get(url)
        
        data, png = obtener_estructura_dom(driver)
        
    except Exception as e:
        print(f"❌ Error al inicializar/ejecutar Selenium en {url}: {e}")
        data = [{'selector': 'FATAL ERROR', 'y': 0, 'height': 0, 'x': 0, 'width': 0}]
    
    finally:
        if driver:
            driver.quit()
            
    return data, png

# === SCRIPT PRINCIPAL ===

if __name__ == "__main__":
    
    # 1. MAPEO DE URLS A TESTEAR 
    BASE_URLS_MAP = {
          "https://artear-tn-sandbox.cdn.arcpublishing.com/": "Homepage",
          "https://artear-tn-sandbox.cdn.arcpublishing.com/ultimas-noticias/": "Listado",
          "https://artear-tn-sandbox.cdn.arcpublishing.com/videos/": "Videos",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/envivo/24hs/": "Vivo",
          "https://artear-tn-sandbox.cdn.arcpublishing.com/clima/": "Clima",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/economia/divisas/dolar-oficial-hoy/": "Divisas",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/podcasts/2025/05/14/soy-adoptada-una-identidad-dicha-con-orgullo/": "Podcast",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/deportes/estadisticas/": "Estadisticas",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/quinielas-loterias/": "Quinielas",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/juegos/": "Juegos",
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/elecciones-2025/": "Elecciones",
          # TIPOS DE NOTAS
          # Article
          "https://artear-tn-sandbox.cdn.arcpublishing.com/internacional/2024/12/09/los-rebeldes-sirios-apuran-la-transicion-para-evitar-conflictos-internos-y-la-irrupcion-del-estado-islamico/": "Article",
          # AMP
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/politica/2024/12/09/el-gobierno-salio-al-cruce-de-kicillof-tras-el-anuncio-de-que-quiere-quedarse-con-aerolineas-argentinas/?outputType=amp": "AMP",
          # Video
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/videos/deportes/futbol/2025/11/10/preocupacion-en-river-maxi-meza-se-lesiono-en-el-superclasico-y-salio-de-la-cancha-llorando/": "Video",
          # Video Dark
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/videos/autos/clasicos/2024/05/09/la-inedita-foto-del-nuevo-suv-con-estilo-coupe-que-este-ano-llegara-a-la-argentina/": "Video Dark",
          # Longform c/fondo
          "https://artear-tn-sandbox.cdn.arcpublishing.com/policiales/2025/05/13/estafadores-de-america-asi-opera-la-banda-que-ofrece-prestamos-millonarios-con-el-cuento-de-la-caja-fuerte/": "Longform c/fondo",
          # Longform s/fondo
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/deportes/2025/01/30/se-avecina-un-temporal-que-pondra-a-prueba-a-varias-regiones-del-pais/": "Longform s/fondo",
          # Liveblogging
          "https://artear-tn-sandbox.cdn.arcpublishing.com/deportes/futbol/2025/11/13/inglaterra-vs-serbia-en-vivo-por-la-fase-de-grupos-de-las-eliminatorias-uefa-hora-donde-ver-y-formaciones/": "Liveblogging",
          # Newsletter
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/policiales/2024/03/26/suscribite-y-recibi-los-newsletters-de-canaletti-los-casos-policiales-que-mas-repercusion-traen-a-la-sociedad-argentina/": "Newsletter",
          # Historia
         # "https://artear-tn-sandbox.cdn.arcpublishing.com/autor/2025/02/21/test-historia/": "Historia",
          # Recipe
          # No aplica a TN. 
              }
    
    
    # 2. MANEJO DEL ARGUMENTO DE LÍNEA DE COMANDOS (Recibe la versión)
    
    if len(sys.argv) < 2:
        print("\n❌ ERROR: Debe proporcionar el número de versión como argumento.")
        print("Uso: python regresion3.py [NUMERO_DE_VERSION]")
        print("Ejemplo: python regresion3.py 170")
        sys.exit(1)

    version_number = sys.argv[1]
    
    try:
        if not version_number.isdigit() or not version_number:
            raise ValueError("El argumento de versión debe ser numérico y no puede estar vacío.")
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
        
    # --- FIN DE MANEJO DEL ARGUMENTO ---

    # GENERAR TIMESTAMP ÚNICO PARA ESTA EJECUCIÓN
    TIMESTAMP_EJECUCION = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    all_comparisons_data = [] 
    start_time_global = time.time()

    print(f"\n INICIANDO PROCESO DE REGRESIÓN DESKTOP - SBX VERSIÓN {version_number}\n ")    
    
    # 3. LOOPEAR Y EJECUTAR PRUEBA PARA CADA URL BASE
    for idx, (base_url, url_description) in enumerate(BASE_URLS_MAP.items()):
        
        start_time_url = time.time() 
        url_id = re.sub(r'[^a-zA-Z0-9]', '_', url_description).lower()

        # 3.1 Construir URLs
        
        # V1: URL con el parámetro de versión (la que quieres probar)
        if '?' in base_url:
            url1 = f"{base_url}&d={version_number}"
        else:
            url1 = f"{base_url}?d={version_number}"

        # V2: URL sin el parámetro de versión (la base actual/sbx)
        url2 = base_url
        
        domain_name = re.sub(r'[^a-zA-Z0-9]', '_', url1.split('//')[-1]).strip('_')
        if not domain_name: domain_name = "home"
        domain_name = domain_name[:40]

        print(f"\n==================================================================================")
        print(f"[{idx + 1}/{len(BASE_URLS_MAP)}] | Página: {url_description}")


        # 3.2 Obtener Datos DOM y Capturas
        print("  [V1] Obteniendo datos estructurales...")
        data_v1, png_v1 = ejecutar_selenium_para_estructura(url1)
        
        print("\n  [V2] Obteniendo datos estructurales...")
        data_v2, png_v2 = ejecutar_selenium_para_estructura(url2)

        
        # 3.3 Comparar Estructuras
        if 'FATAL ERROR' in [d['selector'] for d in data_v1 + data_v2 if isinstance(d.get('selector'), str)]:
            fallas = [{'selector': 'FATAL ERROR (Revisar logs)', 'tipo': 'DIFERENCIA AGRUPADA GRAVE', 'diff': 'N/A', 'v1': 'N/A', 'v2': 'Error grave en la ejecución de Selenium.', 'coords_v2': {'x':0, 'y':0, 'width':0, 'height':0}}]
            selectores_fallidos = []
        else:
            print("\n  🔍 Comparando estructuras DOM (X, Y, W, H)...")
            fallas, selectores_fallidos = comparar_estructura_dom(data_v1, data_v2, UMBRAL_PIXELES_TOLERANCIA)


        # 3.4 Filtrado de fallas no marcables
        fallas_filtradas = []
        for f in fallas:
            coords = f.get('coords_v2', {'x':0, 'y':0, 'width':0, 'height':0})
            x1 = int(coords['x'])
            y1 = int(coords['y'])
            x2 = int(coords['x'] + coords['width'])
            y2 = int(coords['y'] + coords['height'])
            
            if x2 > x1 and y2 > y1:
                fallas_filtradas.append(f)
        
        fallas = fallas_filtradas 
        
        # 3.5 Marcado Visual en la Captura de V2 (Ahora usa la lista filtrada)
        png_v2_marcado = png_v2
        # Las fallas graves son aquellas cuyo tipo contiene 'GRAVE'
        fallas_graves = [f for f in fallas if 'GRAVE' in f['tipo']] 
        
        if fallas:
            print(f"  🔴🔵 Marcando visualmente las diferencias en la captura V2 (si existen)")
            png_v2_marcado = marcar_fallas_en_captura(png_v2, fallas, data_v2) 
            
        
        # 3.6 Reporte y Métrica
        end_time_url = time.time()
        time_elapsed_url = end_time_url - start_time_url
        
        final_alert_color = "red" if fallas_graves else "green"
        
        # Guardar capturas de pantalla 
        # NOTA: Nombres y asignación para el reporte invertido:
        # filename2_diff: V1 (Con versión) SIN marcar.
        # filename1: V2 (Base sin versión) MARCADA.
        # --- CORRECCIÓN SOLICITADA: Usar 'url_id' en lugar de 'domain_name' ---
        filename2_diff = f"{url_id}_V{version_number}_base_{TIMESTAMP_EJECUCION}.png" 
        filename1 = f"{url_id}_V{version_number}_diff_{TIMESTAMP_EJECUCION}.png" 
        # ---------------------------------------------------------------------
        
        if png_v1: Image.open(io.BytesIO(png_v1)).save(os.path.join(output_dir, filename2_diff)) 
        if png_v2_marcado: 
            Image.open(io.BytesIO(png_v2_marcado)).save(os.path.join(output_dir, filename1)) 

        # Generar HTML de las fallas detallado
        fallas_html_detalle = "<ul>"
        
        for i, f in enumerate(fallas):
            coords = f.get('coords_v2', {'x':0, 'y':0, 'width':0, 'height':0})
            
            item_v2_original = next((item for item in data_v2 if item['selector'] == f['selector']), None)
            
            # --- Construcción del selector simplificado ---
            display_selector = ""
            if item_v2_original:
                if item_v2_original.get('class_attr'):
                    display_selector += f"class={item_v2_original['class_attr'][:50]}"
                
                if item_v2_original.get('id_attr'):
                    if display_selector:
                        display_selector += " / "
                    display_selector += f"id={item_v2_original['id_attr']}"
                
                if not display_selector:
                    display_selector = f['selector'].split(' > ')[-1]
            else:
                 display_selector = f['selector'][:50] + "..."
            # -----------------------------------------------------------------

            coords_str = f"{int(coords['x'])},{int(coords['y'])},{int(coords['width'])},{int(coords['height'])}"

            # Usar el nuevo campo 'tipo' para determinar el color (DIFERENCIA AGRUPADA GRAVE/MENOR)
            color = 'red' if 'GRAVE' in f['tipo'] else '#007bff' 
            
            detalle_consolidado = f['v2'] 
            tipo_resumen = f['tipo'].replace('AGRUPADA ', '')
            
            fallas_html_detalle += f"""
            <li class='diff-item' 
                style='color: {color}; border-bottom: 1px dotted #ccc; padding: 5px 0; cursor: pointer;'
                onclick="highlightElement('{url_id}', '{coords_str}', this)"
                data-coords="{coords_str}"
                data-selector="{f['selector']}"
                data-id="item-{url_id}-{i}"
                >
                <span style="font-weight: bold;">Elemento:</span> <code>{display_selector}</code> 
                <br><span style="font-weight: bold;">Resultado Agrupado:</span> <span style='color:{color};'>{tipo_resumen}</span>
                {detalle_consolidado}
            </li>
            """
        if not fallas:
             fallas_html_detalle += "<li>✅ No se encontraron diferencias.</li>"
        fallas_html_detalle += "</ul>"
        
        # Almacenar métricas
        all_comparisons_data.append({
            'base_url': base_url,
            'description': url_description, 
            'url1': url1,
            'url2': url2,
            # Contamos solo fallas graves para el resultado final
            'diff_count': len(fallas_graves), 
            'alert_color': final_alert_color, 
            'html_fallas_detalle': fallas_html_detalle,
            'filename2_diff': filename2_diff, # Usa el nombre de archivo ÚNICO
            'filename1': filename1, # Usa el nombre de archivo ÚNICO
            'time_elapsed': format_time(time_elapsed_url),
            'url_id': url_id 
        })
        
        # Salida en Consola
        if final_alert_color == 'red':
            result_msg = f'❌ SE DETECTARON {len(fallas_graves)} DIFERENCIAS (Graves/Ausentes/Nuevos)'
            result_color = '\033[91m' 
        else:
            result_msg = '✅ PASÓ LA PRUEBA'
            result_color = '\033[92m' 
        
        print(f"\n  {result_color}RESULTADO: {result_msg}\033[0m")
        print(f"  Tiempo total para esta URL: {format_time(time_elapsed_url)}\n")
    
    
    # 4. GENERAR REPORTE HTML FINAL
    
    end_time_global = time.time()
    time_elapsed_global = end_time_global - start_time_global
    
    # El timestamp del archivo HTML es el mismo de las capturas
    timestamp_html = TIMESTAMP_EJECUCION
    html_file = os.path.join(output_dir, f"Reporte_DOM_Estructural_v{version_number}_{timestamp_html}.html")
    
    formatted_time_global = format_time(time_elapsed_global)
    
    # --- Generación del contenido detallado por URL ---
    all_details_html = ""
    sites_with_red_diff = sum(1 for data in all_comparisons_data if data['alert_color'] == 'red')
    
    for data in all_comparisons_data:
        
        display_name = data.get('description', data['base_url']) 
        
        if data['alert_color'] == 'red':
            result_summary_text = f"❌ Se detectaron {data['diff_count']} diferencias graves."
            result_color_style = "red"
        else:
            result_summary_text = "✅ No se encontraron diferencias graves."
            result_color_style = "green"
            
            
        result_summary = f"""
        <p><strong>URL Base (V1):</strong> <code>{data['url1']}</code></p>
        <p><strong>URL Comparada (V2):</strong> <code>{data['url2']}</code></p>
        <p>
            <strong>Resultado:</strong> 
            <span style="font-weight: bold; color: {result_color_style}">
            {result_summary_text}
            </span>
        </p>
        <p><strong>Tiempo de Ejecución:</strong> {data['time_elapsed']}</p> 
        """
        
        all_details_html += f"""
        <div style="border: 2px solid #ddd; padding: 15px; margin-top: 20px; border-radius: 8px;">
            <h2>{display_name}</h2>
            {result_summary}
            
            <details>
                <summary style="cursor: pointer; font-weight: bold; color: #1e3a8a; display: flex; align-items: center;">
                    Detalle de diferencias (Rojo: Grave, Azul: Desplazamiento Menor)
                    <span class="arrow-icon" style="font-size: 1.2em; margin-left: 10px; transition: transform 0.2s; display: inline-block;">&#9660;</span>
                </summary>
                <div id="diff-list-{data['url_id']}" class="diff-container" style="margin-top: 10px; background: #fff; padding: 10px; border: 1px solid #eee;">
                    {data['html_fallas_detalle']}
                </div>
            </details>

            <details>
                <summary style="cursor: pointer; font-weight: bold; color: #1e3a8a; display: flex; align-items: center;">
                    Contexto Visual
                    <span class="arrow-icon" style="font-size: 1.2em; margin-left: 10px; transition: transform 0.2s; display: inline-block;">&#9660;</span>
                </summary>
            <div class='container' id='container-{data['url_id']}'>
                <div>
                    <h4>Versión No Promovida(V1)</h4>
                    <img src='{data['filename2_diff']}' alt='Versión 1'>
                </div>
                <div id="image-container-{data['url_id']}" style="position: relative;">
                    <h4>Versión Promovida (V2) - Diferencias graves (Rojo) o menores (Azul)</h4>
                    <img id="screenshot-{data['url_id']}" src='{data['filename1']}' alt='Versión 2 (Diferencias)'>
                    <div id="highlight-box-{data['url_id']}" class="highlight-box" style="display: none;"></div>
                </div>
            </div>
            </details>

        </div>
        """
        
    
    # --- Estructura Final del HTML y JS de Interacción ---
    global_result_color = 'red' if sites_with_red_diff > 0 else 'green'
    global_result_text = f'❌ Se encontraron diferencias graves en {sites_with_red_diff} de {len(BASE_URLS_MAP)} urls.' if sites_with_red_diff > 0 else '✅ Todas las URLs pasaron la prueba estructural (no se detectaron diferencias graves).'

    html_summary = f"""
    <p><strong>Versión Testeada:</strong> <code>{version_number}</code></p>
    <p><strong>Fecha y Hora de Ejecución:</strong> {format_date(timestamp_html)} {timestamp_html.split('_')[1][:2]}:{timestamp_html.split('_')[1][2:4]}:{timestamp_html.split('_')[1][4:6]}</p> 
    <p><strong>Tiempo Total de Proceso:</strong> {formatted_time_global}</p> 
    <p>
        <strong>Umbral de Tolerancia:</strong> {UMBRAL_PIXELES_TOLERANCIA} píxeles.
    </p>
    <p>
        <strong>Resumen global:</strong> 
        <span style="font-weight: bold; color: {global_result_color}">
        {global_result_text}
        </span>
    </p>

    """
    
    # INYECCIÓN DEL SCRIPT JAVASCRIPT
    javascript_code = """
    <script>
        let lastHighlightedItem = null;
        const ARROW_HEIGHT = -5; 

        function highlightElement(urlId, coordsStr, clickedItem) {
            // 1. Limpiar el resaltado anterior
            if (lastHighlightedItem) {
                lastHighlightedItem.style.backgroundColor = 'transparent';
            }

            // 2. Resaltar el elemento clicado en la lista (visual feedback)
            clickedItem.style.backgroundColor = '#fffacd'; 
            lastHighlightedItem = clickedItem;

            // 3. Obtener elementos de la imagen y el box
            const imageContainer = document.getElementById(`image-container-${urlId}`); 
            const screenshot = document.getElementById(`screenshot-${urlId}`);
            const highlightBox = document.getElementById(`highlight-box-${urlId}`);
            
            if (!screenshot || !highlightBox || !imageContainer) return;

            // 4. Obtener las coordenadas originales (en píxeles de la captura completa)
            const coords = coordsStr.split(',').map(Number);
            const [origX, origY, origW, origH] = coords;
            
            // 5. Calcular la escala 
            const displayedWidth = screenshot.clientWidth;
            const originalWidth = 1920; 
            
            if (displayedWidth > 0 && originalWidth > 0) {
                const scaleFactor = displayedWidth / originalWidth; 

                // 6. Aplicar el factor de escala a las coordenadas
                const scaledX = origX * scaleFactor;
                const scaledY = origY * scaleFactor;
                
                // 7. POSICIONAMIENTO DE LA FLECHA 
                
                highlightBox.style.display = 'block';
                
                // Posición X: Centro horizontal del recuadro rojo/azul
                highlightBox.style.left = `${scaledX + (origW * scaleFactor / 2)}px`; 
                
                // Posición Y CORREGIDA
                highlightBox.style.top = `${scaledY - ARROW_HEIGHT}px`; 
                
                // 8. SCROLL AUTOMÁTICO 
                
                const imageContainerRect = imageContainer.getBoundingClientRect();
                
                const targetY = window.pageYOffset + imageContainerRect.top + scaledY; 

                window.scrollTo({
                    top: targetY - 100, 
                    behavior: 'smooth'
                });
                
            } else {
                highlightBox.style.display = 'none';
                console.error("No se pudo obtener el ancho de la imagen para calcular la escala.");
            }
        }
        
        // Función para reposicionar el highlight si se redimensiona la ventana
        window.addEventListener('resize', () => {
             if (lastHighlightedItem) {
                const coordsStr = lastHighlightedItem.getAttribute('data-coords');
                let listContainer = lastHighlightedItem.closest('.diff-container');
                if (listContainer) {
                    const urlId = listContainer.id.replace('diff-list-', '');
                    highlightElement(urlId, coordsStr, lastHighlightedItem); 
                }
            }
        });
        
        // Corrección de la flecha del summary
        document.querySelectorAll('details').forEach(detail => {
            const arrow = detail.querySelector('.arrow-icon');
            // Inicializa la flecha si el detalle está abierto
            if (detail.open && arrow) {
                 arrow.style.transform = 'rotate(180deg)';
            }
            detail.addEventListener('toggle', () => {
                if (arrow) {
                    arrow.style.transform = detail.open ? 'rotate(180deg)' : 'rotate(0deg)';
                }
            });
        });
        
        // --- Código para el botón Volver Arriba ---
        const scrollButton = document.getElementById('scrollToTopBtn');

        // Mostrar u ocultar el botón basado en la posición de scroll
        window.onscroll = function() {
            if (document.body.scrollTop > 20 || document.documentElement.scrollTop > 20) {
                scrollButton.style.display = "block";
            } else {
                scrollButton.style.display = "none";
            }
        };

        // Al hacer clic, desplaza suavemente al principio de la página
        scrollButton.onclick = function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        };
        // -----------------------------------------------

    </script>
    """

    html = f"""
    <html>
    <head>
    <meta charset="utf-8">
    <title>Reporte de Regresión Desktop - Sbx Versión {version_number}</title>
    <style>
    body {{ font-family: Arial; background: #f7f7f7; margin: 20px; }}
    h1 {{ color: #1e3a8a; border-bottom: 3px solid #bfdbfe; padding-bottom: 10px; }}
    h2 {{ margin-top: 40px; color: #555; border-bottom: 2px solid #ccc; padding-bottom: 5px; }}
    h3, h4 {{ color: #000; margin-top: 10px; margin-bottom: 5px; font-size: 1em; }}
    code {{ background-color: #eee; padding: 2px 4px; border-radius: 3px; }}
    details > summary {{ list-style: none; }} 
    
    /* Reglas del contenedor para el scroll */
    .container {{ 
        display: flex; 
        gap: 20px; 
        margin-bottom: 40px; 
        align-items: flex-start;
        border: 1px solid #eee;
        padding: 10px;
        background: #fafafa;
        border-radius: 4px;
        overflow-x: auto; 
        overflow-y: hidden; 
    }}
    .container > div {{ 
        flex: 1; 
        min-width: 480px; 
    }}
    /* Contenedor de la imagen V2 */
    div[id^="image-container-"] {{
        position: relative; 
        border: 1px solid #ddd;
    }}
    img {{ 
        width: 100%; 
        height: auto;
        border: 3px solid #ccc; 
        border-radius: 4px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
        display: block;
    }}
    
    /* Estilo de la Flecha Indicadora */
    .highlight-box {{
        position: absolute;
        pointer-events: none; 
        z-index: 1000;
        opacity: 1; 
        border-left: 15px solid transparent; 
        border-right: 15px solid transparent; 
        border-top: 30px solid #ffcc00; /* Amarillo */
        transform: translateX(-50%); 
        filter: drop-shadow(0px 0px 5px rgba(0, 0, 0, 0.5));
    }}
    
    /* Estilo para el botón de volver arriba (NUEVO) */
    #scrollToTopBtn {{
        display: none; 
        position: fixed;
        bottom: 20px;
        right: 30px;
        z-index: 99;
        border: none;
        outline: none;
        background-color: #1e3a8a; 
        color: white;
        cursor: pointer;
        padding: 15px;
        border-radius: 50%; 
        font-size: 18px;
        line-height: 0; 
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        transition: background-color 0.3s, opacity 0.3s;
    }}
    #scrollToTopBtn:hover {{
        background-color: #3b82f6; 
    }}
    </style>
    </head>
    <body>
    <h1>Reporte de Regresión - Desktop Sbx - Versión {version_number}</h1>
    {html_summary}
    <hr style="margin-top: 20px; margin-bottom: 20px;"/>
    
    <div id="report-details-container">
        {all_details_html}
    </div>
    
    <button id="scrollToTopBtn" title="Ir Arriba">↑</button> 
    
    {javascript_code}  
    
    </body>
    </html>
    """
    
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n==================================================================================")
    print(f"✅ Proceso de regresión visual completado.")
    print(f"📄 Reporte generado en: {html_file}")

    print(f"==================================================================================")
