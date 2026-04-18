# -*- coding: utf-8 -*-
"""Improved screenshot capture with scrollIntoView for each section."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

IMG = 'docs/images'
URL = 'http://localhost:3838'
RESULT_FOLDER = r'C:\Users\Cciia\Biochem Dropbox\木津亮馬\UMAP_Claudecode\TIMS\Data\240819_py_PD100um_251223\260219-DEMO'

def shot(pg, name):
    pg.screenshot(path=f'{IMG}/{name}', full_page=False)
    print(f'  saved: {name}')

def js_click(pg, js_expr, wait=2):
    pg.evaluate(js_expr)
    time.sleep(wait)

def scroll_to_element(pg, js_selector, offset_y=-50):
    """Scroll element into view with optional offset."""
    pg.evaluate(f"""
        const el = {js_selector};
        if (el) {{
            el.scrollIntoView({{ behavior: 'instant', block: 'start' }});
            window.scrollBy(0, {offset_y});
        }}
    """)
    time.sleep(1)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1400, 'height': 900})

    # Navigate to app
    page.goto(URL, wait_until='networkidle', timeout=30000)
    time.sleep(3)

    # Go to sub-project
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.trim() === '\u958b\u304f');
        if (b) b.click();
    """)

    # Enter analysis view
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.trim() === '\u89e3\u6790');
        if (b) b.click();
    """)

    # Go to interactive tab
    js_click(page, """
        const tabs = [...document.querySelectorAll('.nav-link')];
        const t = tabs.find(e => e.textContent.includes('\u30a4\u30f3\u30bf\u30e9\u30af\u30c6\u30a3\u30d6'));
        if (t) t.click();
    """)

    # Set result folder path and click scan
    print('[1] Setting result folder...')
    result_input = page.query_selector('#interactive_result_folder')
    if not result_input:
        result_input = page.query_selector('input[placeholder*="\u7d50\u679c\u30d5\u30a9\u30eb\u30c0"]')
    if result_input:
        result_input.fill(RESULT_FOLDER)
        print('  filled result folder path')
    else:
        page.evaluate(f"""
            const inputs = [...document.querySelectorAll('input')];
            const inp = inputs.find(e => e.placeholder && e.placeholder.includes('\u30d5\u30a9\u30eb\u30c0'));
            if (inp) {{
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeInputValueSetter.call(inp, '{RESULT_FOLDER.replace(chr(92), chr(92)+chr(92))}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        """)
        print('  filled via JS')

    time.sleep(1)

    # Click scan button
    print('[2] Scanning...')
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.includes('\u30b9\u30ad\u30e3\u30f3'));
        if (b) b.click();
    """, 3)

    # Load data
    print('[3] Loading data...')
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.includes('\u8aad\u307f\u8fbc\u3080'));
        if (b) b.click();
    """, 15)  # Data loading can take time

    # Open ALL accordion sections at once
    print('[4] Opening all accordion sections...')
    page.evaluate("""
        const btns = [...document.querySelectorAll('.accordion-button')];
        btns.forEach(b => {
            if (b.classList.contains('collapsed')) b.click();
        });
    """)
    time.sleep(8)  # Wait for all plots to render

    # === UMAP section: scroll to UMAP plot area ===
    print('[5] UMAP plot...')
    scroll_to_element(page, """
        (() => {
            const btns = [...document.querySelectorAll('.accordion-button')];
            return btns.find(e => e.textContent.includes('UMAP') && !e.textContent.includes('Spatial'));
        })()
    """, -10)
    time.sleep(1)
    shot(page, '08a_umap_controls.png')

    # Scroll down to see the actual UMAP scatter plot
    page.evaluate("""
        const plots = document.querySelectorAll('.js-plotly-plot');
        if (plots.length > 0) {
            plots[0].scrollIntoView({ behavior: 'instant', block: 'center' });
        }
    """)
    time.sleep(2)
    shot(page, '08b_umap_plot.png')

    # === Spatial Mapping section ===
    print('[6] Spatial Mapping...')
    scroll_to_element(page, """
        (() => {
            const btns = [...document.querySelectorAll('.accordion-button')];
            return btns.find(e => e.textContent.includes('Spatial'));
        })()
    """, -10)
    time.sleep(1)
    shot(page, '09a_spatial_controls.png')

    # Scroll to spatial plots
    page.evaluate("""
        const spatialBtn = [...document.querySelectorAll('.accordion-button')].find(e => e.textContent.includes('Spatial'));
        if (spatialBtn) {
            const section = spatialBtn.closest('.accordion-item');
            if (section) {
                const plots = section.querySelectorAll('.js-plotly-plot');
                if (plots.length > 0) {
                    plots[0].scrollIntoView({ behavior: 'instant', block: 'center' });
                }
            }
        }
    """)
    time.sleep(2)
    shot(page, '09b_spatial_plot.png')

    # === Feature Plot section ===
    print('[7] Feature Plot...')
    scroll_to_element(page, """
        (() => {
            const btns = [...document.querySelectorAll('.accordion-button')];
            return btns.find(e => e.textContent.includes('Feature'));
        })()
    """, -10)
    time.sleep(1)
    shot(page, '10a_feature_controls.png')

    # Scroll to feature plot area
    page.evaluate("""
        const featureBtn = [...document.querySelectorAll('.accordion-button')].find(e => e.textContent.includes('Feature'));
        if (featureBtn) {
            const section = featureBtn.closest('.accordion-item');
            if (section) {
                const plots = section.querySelectorAll('.js-plotly-plot');
                if (plots.length > 0) {
                    plots[0].scrollIntoView({ behavior: 'instant', block: 'center' });
                }
            }
        }
    """)
    time.sleep(2)
    shot(page, '10b_feature_plot.png')

    # === DEG section ===
    print('[8] DEG...')
    scroll_to_element(page, """
        (() => {
            const btns = [...document.querySelectorAll('.accordion-button')];
            return btns.find(e => e.textContent.includes('DEG'));
        })()
    """, -10)
    time.sleep(1)
    shot(page, '11a_deg_controls.png')

    # Scroll to DEG plots
    page.evaluate("""
        const degBtn = [...document.querySelectorAll('.accordion-button')].find(e => e.textContent.includes('DEG'));
        if (degBtn) {
            const section = degBtn.closest('.accordion-item');
            if (section) {
                const plots = section.querySelectorAll('.js-plotly-plot');
                if (plots.length > 0) {
                    plots[0].scrollIntoView({ behavior: 'instant', block: 'center' });
                }
            }
        }
    """)
    time.sleep(2)
    shot(page, '11b_deg_plot.png')

    browser.close()
    print('\nDone!')
