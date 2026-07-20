from flask import Flask, request, jsonify
from playwright.async_api import async_playwright
import asyncio
import base64
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

ANALIZZA_LOGIN_URL = 'https://www.analizzascore.com.br/auth/login.php'
ANALIZZA_BASE = 'https://www.analizzascore.com.br'
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

@app.route('/diagnostico', methods=['GET'])
def diagnostico():
    """Abre a página de login e retorna o HTML para diagnóstico"""
    try:
        resultado = asyncio.run(capturar_login())
        return jsonify(resultado)
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

async def capturar_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        page = await browser.new_page()
        try:
            await page.goto(ANALIZZA_LOGIN_URL, wait_until='networkidle', timeout=30000)
            html = await page.content()
            inputs = await page.query_selector_all('input')
            campos = []
            for inp in inputs:
                name = await inp.get_attribute('name')
                type_ = await inp.get_attribute('type')
                id_ = await inp.get_attribute('id')
                campos.append({'name': name, 'type': type_, 'id': id_})
            forms = await page.query_selector_all('form')
            form_actions = []
            for f in forms:
                action = await f.get_attribute('action')
                method = await f.get_attribute('method')
                form_actions.append({'action': action, 'method': method})
            return {
                'ok': True,
                'url_atual': page.url,
                'campos': campos,
                'forms': form_actions,
                'html_trecho': html[:2000]
            }
        finally:
            await browser.close()

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
            # LOGIN
            logger.info('Acessando login: ' + ANALIZZA_LOGIN_URL)
            await page.goto(ANALIZZA_LOGIN_URL, wait_until='networkidle', timeout=30000)
            logger.info('Página carregada. URL: ' + page.url)

            # Pega todos os inputs para debug
            inputs = await page.query_selector_all('input')
            for inp in inputs:
                name = await inp.get_attribute('name')
                type_ = await inp.get_attribute('type')
                logger.info(f'Input encontrado: name={name} type={type_}')

            # Preenche usuário
            usuario_field = await page.query_selector('input[name="usuario"]') or \
                           await page.query_selector('input[type="text"]') or \
                           await page.query_selector('input[name="user"]') or \
                           await page.query_selector('input[name="login"]')

            if not usuario_field:
                raise Exception('Campo de usuário não encontrado na página de login')

            await usuario_field.fill(ANALIZZA_USUARIO)
            logger.info('Usuário preenchido')

            # Preenche senha
            senha_field = await page.query_selector('input[name="senha"]') or \
                         await page.query_selector('input[type="password"]')

            if not senha_field:
                raise Exception('Campo de senha não encontrado na página de login')

            await senha_field.fill(ANALIZZA_SENHA)
            logger.info('Senha preenchida')

            # Clica em entrar
            btn = await page.query_selector('button[type="submit"]') or \
                  await page.query_selector('input[type="submit"]') or \
                  await page.query_selector('button:has-text("Entrar")') or \
                  await page.query_selector('.btn-login')

            if btn:
                await btn.click()
            else:
                await page.keyboard.press('Enter')

            await page.wait_for_load_state('networkidle', timeout=15000)
            logger.info('Após login. URL: ' + page.url)

            # Verifica se login foi bem sucedido
            if 'login' in page.url.lower():
                html = await page.content()
                logger.info('Ainda na página de login. HTML: ' + html[:500])
                raise Exception('Login falhou — ainda na página de login após submissão')

            pdfs = {}

            # SCPC
            if consultar_scpc:
                logger.info('Consultando SCPC...')
                pdf_scpc = await consultar_e_baixar(page, ANALIZZA_BASE + URLS[tipo + '_SCPC'], documento, tipo)
                if pdf_scpc:
                    pdfs['scpc'] = pdf_scpc
                    logger.info('SCPC OK')

            # SCR
            if consultar_scr:
                logger.info('Consultando SCR...')
                pdf_scr = await consultar_e_baixar(page, ANALIZZA_BASE + URLS[tipo + '_SCR'], documento, tipo)
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
    await page.goto(url, wait_until='networkidle', timeout=30000)
    await asyncio.sleep(1)
    logger.info('Página consulta carregada: ' + page.url)

    # Digita o documento
    campo = None
    for seletor in [f'input[name="{tipo.lower()}"]', 'input[name="cpf"]', 'input[name="cnpj"]', 'input[type="text"]:visible']:
        try:
            campo = await page.query_selector(seletor)
            if campo:
                break
        except:
            pass

    if campo:
        await campo.fill(documento)
        logger.info('Documento preenchido')
    else:
        logger.warning('Campo de documento não encontrado')

    # Clica em Consultar
    for seletor in ['button:has-text("Consultar")', 'input[value="Consultar"]', 'button[type="submit"]', '.btn-consultar']:
        try:
            btn = await page.query_selector(seletor)
            if btn:
                await btn.click()
                break
        except:
            pass

    await page.wait_for_load_state('networkidle', timeout=30000)
    await asyncio.sleep(2)

    content = await page.content()

    if 'Falha' in content or 'não retornou sucesso' in content:
        logger.warning('Consulta retornou falha')
        return None

    # Tenta baixar PDF
    for seletor in ['a:has-text("Baixar")', 'button:has-text("Baixar")', 'a[href*="pdf"]', 'a[href*="download"]', '.btn-baixar']:
        try:
            elemento = await page.query_selector(seletor)
            if elemento:
                async with page.expect_download(timeout=30000) as download_info:
                    await elemento.click()
                download = await download_info.value
                path = f'/tmp/{download.suggested_filename}'
                await download.save_as(path)
                with open(path, 'rb') as f:
                    logger.info('PDF baixado com sucesso')
                    return base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            logger.warning(f'Seletor {seletor} falhou: {e}')
            continue

    # Fallback: screenshot
    logger.info('PDF não encontrado, usando screenshot')
    screenshot = await page.screenshot(full_page=True)
    return base64.b64encode(screenshot).decode('utf-8')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
