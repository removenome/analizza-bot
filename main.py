from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import base64
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

ANALIZZA_BASE = 'https://www.analizzascore.com.br/auth/login.php'
ANALIZZA_USUARIO = os.environ.get('ANALIZZA_USUARIO', 'remove')
ANALIZZA_SENHA = os.environ.get('ANALIZZA_SENHA', '291185')

URLS = {
    'CPF_SCPC': '/cliente/consultas/spc_cpf_score_2.php?sid=1&gid=1',
    'CPF_SCR':  '/cliente/consultas/scr_cpf.php?sid=8&gid=1',
    'CNPJ_SCPC': '/cliente/consultas/cnpj_score.php?sid=3&gid=1',
    'CNPJ_SCR':  '/cliente/consultas/scr_cnpj.php?sid=12&gid=1'
}

@app.route('/', methods=['GET'])
def health():
    return jsonify({'ok': True, 'mensagem': 'Analizza Bot ativo.'})

@app.route('/consultar', methods=['POST'])
def consultar():
    dados = request.get_json()
    if not dados:
        return jsonify({'ok': False, 'erro': 'Dados não recebidos'}), 400

    documento = ''.join(filter(str.isdigit, dados.get('documento', '')))
    nome = dados.get('nome', 'Cliente')
    consultar_scpc = dados.get('consultar_scpc', True)
    consultar_scr = dados.get('consultar_scr', True)

    if len(documento) not in [11, 14]:
        return jsonify({'ok': False, 'erro': 'CPF ou CNPJ inválido'}), 400

    tipo = 'CNPJ' if len(documento) == 14 else 'CPF'

    try:
        resultado = asyncio.run(executar_consulta(documento, tipo, nome, consultar_scpc, consultar_scr))
        return jsonify(resultado)
    except Exception as e:
        logger.error(f'Erro: {e}')
        return jsonify({'ok': False, 'erro': str(e)}), 500

async def executar_consulta(documento, tipo, nome, consultar_scpc, consultar_scr):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            accept_downloads=True
        )
        page = await context.new_page()

        try:
            # 1. LOGIN
            logger.info('Fazendo login...')
            await page.goto(ANALIZZA_BASE + '/auth/login.php', wait_until='networkidle')
            await page.fill('input[name="usuario"], input[type="text"]', ANALIZZA_USUARIO)
            await page.fill('input[name="senha"], input[type="password"]', ANALIZZA_SENHA)
            await page.click('button[type="submit"], input[type="submit"], .btn-login, button:has-text("Entrar")')
            await page.wait_for_load_state('networkidle')
            logger.info('Login realizado. URL: ' + page.url)

            pdfs = {}

            # 2. SCPC
            if consultar_scpc:
                logger.info('Consultando SCPC...')
                url_scpc = ANALIZZA_BASE + URLS[tipo + '_SCPC']
                pdf_scpc = await consultar_e_baixar(page, url_scpc, documento, tipo)
                if pdf_scpc:
                    pdfs['scpc'] = pdf_scpc
                    logger.info('SCPC OK')

            # 3. SCR
            if consultar_scr:
                logger.info('Consultando SCR...')
                url_scr = ANALIZZA_BASE + URLS[tipo + '_SCR']
                pdf_scr = await consultar_e_baixar(page, url_scr, documento, tipo)
                if pdf_scr:
                    pdfs['scr'] = pdf_scr
                    logger.info('SCR OK')

            return {
                'ok': True,
                'tipo': tipo,
                'documento': documento,
                'nome': nome,
                'pdf_scpc': pdfs.get('scpc'),
                'pdf_scr': pdfs.get('scr')
            }

        finally:
            await browser.close()

async def consultar_e_baixar(page, url, documento, tipo):
    await page.goto(url, wait_until='networkidle')
    await asyncio.sleep(1)

    # Digita o documento
    campo = 'input[name="cpf"]' if tipo == 'CPF' else 'input[name="cnpj"]'
    try:
        await page.fill(campo, documento)
    except:
        # Tenta campo genérico
        await page.fill('input[type="text"]:visible', documento)

    # Clica em Consultar
    try:
        await page.click('button:has-text("Consultar"), input[value="Consultar"], .btn-consultar')
    except:
        await page.click('button[type="submit"]:visible')

    await page.wait_for_load_state('networkidle')
    await asyncio.sleep(2)

    # Verifica erro
    content = await page.content()
    if 'Falha' in content or 'não retornou sucesso' in content:
        logger.warning('Consulta retornou falha')
        return None

    # Clica em Baixar e pega o PDF
    try:
        async with page.expect_download(timeout=30000) as download_info:
            await page.click('a:has-text("Baixar"), button:has-text("Baixar"), .btn-baixar, a[href*="pdf"], a[href*="download"]')
        download = await download_info.value
        path = f'/tmp/{download.suggested_filename}'
        await download.save_as(path)
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        logger.warning(f'Erro ao baixar PDF: {e}')
        # Tenta capturar screenshot como fallback
        screenshot = await page.screenshot(full_page=True)
        return base64.b64encode(screenshot).decode('utf-8')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
