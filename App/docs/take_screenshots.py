# -*- coding: utf-8 -*-
"""Screenshot capture - fixed tab navigation."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

IMG = 'docs/images'
URL = 'http://localhost:3838'

def shot(pg, name):
    pg.screenshot(path=f'{IMG}/{name}', full_page=False)
    print(f'  saved: {name}')

def js_click(pg, js_expr, wait=2):
    pg.evaluate(js_expr)
    time.sleep(wait)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1400, 'height': 900})

    # === 1. Landing ===
    print('[1] Landing')
    page.goto(URL, wait_until='networkidle', timeout=30000)
    time.sleep(3)
    shot(page, '01_landing.png')

    # === 2. Sub-project page ===
    print('[2] Sub-project')
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.trim() === '\u958b\u304f');
        if (b) b.click();
    """)
    shot(page, '02_subproject.png')

    # === 3. Go into analysis view (click sub-project analysis btn) ===
    print('[3] Enter analysis view')
    js_click(page, """
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find(e => e.textContent.trim() === '\u89e3\u6790');
        if (b) b.click();
    """)
    time.sleep(1)

    # === 3a. Settings tab (already active) ===
    print('[3a] Settings tab')
    shot(page, '03_settings_tab.png')

    # Scroll to show full settings + execution button
    page.evaluate('window.scrollTo(0, 400)')
    time.sleep(1)
    shot(page, '03b_settings_scroll.png')
    page.evaluate('window.scrollTo(0, 0)')
    time.sleep(0.5)

    # === 4. Results tab (click the tab link at top) ===
    print('[4] Results tab')
    js_click(page, """
        const tabs = [...document.querySelectorAll('.nav-link')];
        const t = tabs.find(e => e.textContent.includes('\u7d50\u679c\u95b2\u89a7'));
        if (t) t.click();
    """)
    shot(page, '04_results_tab.png')

    # === 5. Interactive tab ===
    print('[5] Interactive tab')
    js_click(page, """
        const tabs = [...document.querySelectorAll('.nav-link')];
        const t = tabs.find(e => e.textContent.includes('\u30a4\u30f3\u30bf\u30e9\u30af\u30c6\u30a3\u30d6'));
        if (t) t.click();
    """)
    shot(page, '05_interactive_tab.png')

    # === 6. Accordion: Export ===
    print('[6] Export section')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('\u30a8\u30af\u30b9\u30dd\u30fc\u30c8'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    shot(page, '06_export.png')

    # === 7. Accordion: Cluster info ===
    print('[7] Cluster info')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('\u30af\u30e9\u30b9\u30bf\u60c5\u5831'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 200)')
    time.sleep(0.5)
    shot(page, '07_cluster_info.png')

    # === 8. Accordion: UMAP ===
    print('[8] UMAP')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('UMAP') && !e.textContent.includes('Spatial'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 400)')
    time.sleep(0.5)
    shot(page, '08_umap.png')

    # === 9. Accordion: Spatial Mapping ===
    print('[9] Spatial Mapping')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('Spatial'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 600)')
    time.sleep(0.5)
    shot(page, '09_spatial.png')

    # === 10. Accordion: Feature Plot ===
    print('[10] Feature Plot')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('Feature'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 800)')
    time.sleep(0.5)
    shot(page, '10_feature.png')

    # === 11. Accordion: DEG ===
    print('[11] DEG')
    js_click(page, """
        const btns = [...document.querySelectorAll('.accordion-button')];
        const b = btns.find(e => e.textContent.includes('DEG'));
        if (b && b.classList.contains('collapsed')) b.click();
    """, 1)
    page.evaluate('window.scrollTo(0, 1000)')
    time.sleep(0.5)
    shot(page, '11_deg.png')

    # === 12. Session tab ===
    print('[12] Session tab')
    js_click(page, """
        const tabs = [...document.querySelectorAll('.nav-link')];
        const t = tabs.find(e => e.textContent.includes('\u30bb\u30c3\u30b7\u30e7\u30f3'));
        if (t) t.click();
    """)
    page.evaluate('window.scrollTo(0, 0)')
    time.sleep(0.5)
    shot(page, '12_session_tab.png')

    browser.close()
    print('\nAll screenshots captured!')
