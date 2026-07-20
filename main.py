from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import base64
import os
import logging
import re

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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

@app.route('/', methods=['GET'])
def health():
    return jsonify({'ok': True, 'mensagem': 'Analizza Bot ativo.'})

@app.route('/diagnostico', methods=['GET'])
def diagnostico():
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        r = session.get(ANALIZZA_LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        inputs = [{'name': i.get('name'), 'type': i.get('type'), 'id': i.get('id')} for i in soup.find_all('input')]
        forms = [{'action': f.get('action'), 'method': f.get('method')} for f in soup.find_all('form')]
        return jsonify({'ok': True, 'status': r.status_code, 'url': r.url, 'inputs': inputs, 'forms': forms})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

@app.route('/teste-login', methods=['GET'])
def teste_login():
    """Testa o login e retorna resultado detalhado"""
    try:
        session = fazer_login()
        # Tenta acessar uma página protegida
        r = session.get(ANALIZZA_BASE + '/cliente/', timeout=15)
        return jsonify({
            'ok': True,
            'mensagem': 'Login OK',
            'url_pos_login': r.url,
            'status': r.status_code,
            'logado': 'login' not in r.url.lower()
        })
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

@app.route('/consultar', methods=['POST', 'GET'])
def consultar():
    # Aceita tanto POST quanto GET para compatibilidade
    if request.method == 'GET':
        dados = request.args.to_dict()
    else:
        dados = request.get_json(force=True) or request.form.to_dict()

    if not dados:
        return jsonify({'ok': False, 'erro': 'Dados não recebidos'}), 400

    logger.info(f'Recebido: {list(dados.keys())}')

    documento = ''.join(filter(str.isdigit, dados.get('documento', '') or dados.get('cpf', '')))
    nome = dados.get('nome', 'Cliente')
    consultar_scpc = dados.get('consultar_scpc', True)
    consultar_scr = dados.get('consultar_scr', True)

    if isinstance(consultar_scpc, str):
        consultar_scpc = consultar_scpc.lower() != 'false'
    if isinstance(consultar_scr, str):
        consultar_scr = consultar_scr.lower() != 'false'

    if len(documento) not in [11, 14]:
        return jsonify({'ok': False, 'erro': f'CPF/CNPJ inválido: {documento}'}), 400

    tipo = 'CNPJ' if len(documento) == 14 else 'CPF'
    logger.info(f'Consultando {tipo}: {documento}')

    try:
        session = fazer_login()
        pdfs = {}

        if consultar_scpc:
            logger.info('Consultando SCPC...')
            pdf = consultar_e_baixar(session, ANALIZZA_BASE + URLS[tipo + '_SCPC'], documento, tipo)
            if pdf:
                pdfs['scpc'] = pdf
                logger.info(f'SCPC OK, tamanho: {len(pdf)}')

        if consultar_scr:
            logger.info('Consultando SCR...')
            pdf = consultar_e_baixar(session, ANALIZZA_BASE + URLS[tipo + '_SCR'], documento, tipo)
            if pdf:
                pdfs['scr'] = pdf
                logger.info(f'SCR OK, tamanho: {len(pdf)}')

        return jsonify({
            'ok': True,
            'tipo': tipo,
            'documento': documento,
            'nome': nome,
            'pdf_scpc': pdfs.get('scpc'),
            'pdf_scr': pdfs.get('scr')
        })

    except Exception as e:
        logger.error(f'Erro: {e}')
        return jsonify({'ok': False, 'erro': str(e)}), 500

def fazer_login():
    session = requests.Session()
    session.headers.update(HEADERS)

    # GET para pegar cookies e token CSRF
    r = session.get(ANALIZZA_LOGIN_URL, timeout=15)
    logger.info(f'GET login: {r.status_code} | cookies: {dict(session.cookies)}')

    soup = BeautifulSoup(r.text, 'lxml')

    # Monta payload com campos ocultos
    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        value = inp.get('value', '')
        if name and inp.get('type') == 'hidden':
            payload[name] = value

    payload['usuario'] = ANALIZZA_USUARIO
    payload['senha'] = ANALIZZA_SENHA

    # Adiciona campos de checkbox se existirem
    for inp in soup.find_all('input', {'type': 'checkbox'}):
        name = inp.get('name')
        if name and name not in payload:
            payload[name] = ''

    logger.info(f'Payload: {list(payload.keys())}')

    # Action do form
    form = soup.find('form')
    action = ANALIZZA_LOGIN_URL
    if form and form.get('action'):
        action_raw = form['action']
        if action_raw.startswith('http'):
            action = action_raw
        elif action_raw.startswith('/'):
            action = ANALIZZA_BASE + action_raw
        else:
            action = ANALIZZA_LOGIN_URL

    logger.info(f'POST login em: {action}')

    headers_post = {**HEADERS,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': ANALIZZA_BASE,
        'Referer': ANALIZZA_LOGIN_URL,
    }

    r2 = session.post(action, data=payload, headers=headers_post, timeout=15, allow_redirects=True)
    logger.info(f'POST login: {r2.status_code} | URL final: {r2.url}')

    # Verifica se logou com sucesso
    if 'login' in r2.url.lower():
        soup2 = BeautifulSoup(r2.text, 'lxml')
        # Procura mensagem de erro
        for cls in ['alert', 'alert-danger', 'error', 'msg-erro', 'invalid-feedback']:
            el = soup2.find(class_=cls)
            if el:
                logger.error(f'Erro login: {el.get_text().strip()}')
                raise Exception(f'Login falhou: {el.get_text().strip()}')
        raise Exception(f'Login falhou — ainda em {r2.url}')

    logger.info('Login OK!')
    return session

def consultar_e_baixar(session, url, documento, tipo):
    headers_get = {**HEADERS, 'Referer': ANALIZZA_BASE + '/'}

    r = session.get(url, headers=headers_get, timeout=15)
    logger.info(f'GET consulta: {r.status_code} | {r.url}')

    soup = BeautifulSoup(r.text, 'lxml')

    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        value = inp.get('value', '')
        if name:
            if inp.get('type') != 'submit':
                payload[name] = value if inp.get('type') != 'hidden' or value else ''

    # Campo do documento
    campo_doc = tipo.lower()
    payload[campo_doc] = documento
    # Remove campos de submit
    payload = {k: v for k, v in payload.items() if k and k != 'submit'}

    logger.info(f'Payload consulta: {payload}')

    headers_post = {**HEADERS,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': ANALIZZA_BASE,
        'Referer': url,
    }

    r2 = session.post(url, data=payload, headers=headers_post, timeout=30, allow_redirects=True)
    logger.info(f'POST consulta: {r2.status_code}')

    conteudo = r2.text
    if 'Falha' in conteudo or 'não retornou sucesso' in conteudo:
        logger.warning('Consulta retornou falha')
        return None

    soup2 = BeautifulSoup(conteudo, 'lxml')

    # Procura link de download do PDF
    pdf_url = None
    for a in soup2.find_all('a', href=True):
        href = a['href']
        texto = a.get_text(strip=True).lower()
        if any(x in texto for x in ['baixar', 'download', 'pdf', 'salvar']):
            pdf_url = href
            logger.info(f'Link PDF encontrado: {href} | texto: {texto}')
            break
        if any(x in href.lower() for x in ['.pdf', 'pdf', 'download', 'baixar']):
            pdf_url = href
            logger.info(f'Link PDF por href: {href}')
            break

    if pdf_url:
        if not pdf_url.startswith('http'):
            pdf_url = ANALIZZA_BASE + ('/' if not pdf_url.startswith('/') else '') + pdf_url
        r_pdf = session.get(pdf_url, timeout=30)
        logger.info(f'PDF: {r_pdf.status_code} | {len(r_pdf.content)} bytes | content-type: {r_pdf.headers.get("content-type")}')
        return base64.b64encode(r_pdf.content).decode('utf-8')

    # Retorna HTML como fallback
    logger.info('PDF não encontrado, retornando HTML como fallback')
    return base64.b64encode(conteudo.encode('utf-8')).decode('utf-8')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
