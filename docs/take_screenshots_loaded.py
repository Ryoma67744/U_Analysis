# -*- coding: utf-8 -*-
"""Screenshot capture with data loaded into interactive tab."""
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
        # Try via JS
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
    shot(page, '05_interactive_tab.png')

    # Check if method selector appeared and select harmony
    print('[3] Checking for method selector...')
    time.sleep(2)
    shot(page, '05b_method_selector.png')

    # Try to click "load data" button
    print('[4] Loading data...')
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.includes('\u8aad\u307f\u8fbc\u3080'));
        if (b) b.click();
    """, 15)  # Data loading can take time
    shot(page, '05c_data_loaded.png')

    # Now take accordion screenshots
    print('[5] Export section')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('\u30a8\u30af\u30b9\u30dd\u30fc\u30c8'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    shot(page, '06_export.png')

    print('[6] Cluster info')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('\u30af\u30e9\u30b9\u30bf\u60c5\u5831'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 200)')
    time.sleep(0.5)
    shot(page, '07_cluster_info.png')

    print('[7] UMAP')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('UMAP') && !e.textContent.includes('Spatial'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 3)
    page.evaluate('window.scrollTo(0, 400)')
    time.sleep(1)
    shot(page, '08_umap.png')

    print('[8] Spatial')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('Spatial'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 3)
    page.evaluate('window.scrollTo(0, 600)')
    time.sleep(1)
    shot(page, '09_spatial.png')

    print('[9] Feature Plot')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('Feature'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 2)
    page.evaluate('window.scrollTo(0, 800)')
    time.sleep(1)
    shot(page, '10_feature.png')

    print('[10] DEG')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('DEG'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 2)
    page.evaluate('window.scrollTo(0, 1000)')
    time.sleep(1)
    shot(page, '11_deg.png')

    browser.close()
    print('\nDone!')
