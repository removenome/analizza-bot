from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9',
}

@app.route('/', methods=['GET'])
def health():
    return jsonify({'ok': True, 'mensagem': 'Analizza Bot ativo.'})

@app.route('/teste-login', methods=['GET'])
def teste_login():
    try:
        session = fazer_login()
        r = session.get(ANALIZZA_BASE + '/cliente/', timeout=15)
        return jsonify({'ok': True, 'mensagem': 'Login OK', 'url': r.url, 'logado': 'login' not in r.url.lower()})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

@app.route('/teste-consulta', methods=['GET'])
def teste_consulta():
    """Testa a consulta e mostra o HTML retornado pelo Analizza"""
    cpf = request.args.get('cpf', '01548663697')
    try:
        session = fazer_login()
        url = ANALIZZA_BASE + URLS['CPF_SCPC']
        
        # GET na página
        r_get = session.get(url, timeout=15)
        soup = BeautifulSoup(r_get.text, 'lxml')
        
        # Mostra os inputs encontrados
        inputs = []
        for inp in soup.find_all('input'):
            inputs.append({
                'name': inp.get('name'),
                'type': inp.get('type'),
                'value': inp.get('value', '')[:50]
            })
        
        # Monta e faz o POST
        payload = {}
        for inp in soup.find_all('input'):
            name = inp.get('name')
            if name and inp.get('type') != 'submit':
                payload[name] = inp.get('value', '')
        payload['cpf'] = cpf
        
        r_post = session.post(url, data=payload, headers={
            **HEADERS,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': url,
            'Origin': ANALIZZA_BASE
        }, timeout=30)
        
        # Extrai texto do resultado
        soup2 = BeautifulSoup(r_post.text, 'lxml')
        texto = soup2.get_text(separator='\n', strip=True)[:3000]
        
        # Procura links de download
        links = []
        for a in soup2.find_all('a', href=True):
            links.append({'texto': a.get_text(strip=True), 'href': a['href']})
        
        return jsonify({
            'ok': True,
            'cpf_enviado': cpf,
            'inputs_encontrados': inputs,
            'payload_enviado': payload,
            'status_post': r_post.status_code,
            'texto_resultado': texto,
            'links': links[:20]
        })
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

@app.route('/consultar', methods=['POST', 'GET'])
def consultar():
    if request.method == 'GET':
        dados = request.args.to_dict()
    else:
        dados = request.get_json(force=True) or {}

    documento = ''.join(filter(str.isdigit, dados.get('documento', '') or dados.get('cpf', '')))
    nome = dados.get('nome', 'Cliente')
    consultar_scpc = str(dados.get('consultar_scpc', 'true')).lower() != 'false'
    consultar_scr = str(dados.get('consultar_scr', 'true')).lower() != 'false'

    if len(documento) not in [11, 14]:
        return jsonify({'ok': False, 'erro': f'Documento inválido: {documento}'}), 400

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

    r = session.get(ANALIZZA_LOGIN_URL, timeout=15)
    soup = BeautifulSoup(r.text, 'lxml')

    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        if name and inp.get('type') == 'hidden':
            payload[name] = inp.get('value', '')

    payload['usuario'] = ANALIZZA_USUARIO
    payload['senha'] = ANALIZZA_SENHA

    form = soup.find('form')
    action = ANALIZZA_LOGIN_URL
    if form and form.get('action'):
        a = form['action']
        action = a if a.startswith('http') else ANALIZZA_BASE + a

    r2 = session.post(action, data=payload, headers={
        **HEADERS,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': ANALIZZA_BASE,
        'Referer': ANALIZZA_LOGIN_URL
    }, timeout=15, allow_redirects=True)

    logger.info(f'Login: {r2.status_code} | URL: {r2.url}')

    if 'login' in r2.url.lower():
        raise Exception('Login falhou')

    return session

def consultar_e_baixar(session, url, documento, tipo):
    # GET na página de consulta
    r = session.get(url, timeout=15)
    logger.info(f'GET consulta: {r.status_code}')
    soup = BeautifulSoup(r.text, 'lxml')

    # Monta payload completo
    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        if name and inp.get('type') != 'submit':
            payload[name] = inp.get('value', '')

    # Campo do documento
    campo = tipo.lower()
    payload[campo] = documento

    logger.info(f'Payload: {payload}')

    # POST para consultar
    r2 = session.post(url, data=payload, headers={
        **HEADERS,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': ANALIZZA_BASE,
        'Referer': url
    }, timeout=30, allow_redirects=True)

    logger.info(f'POST consulta: {r2.status_code} | URL: {r2.url}')
    html = r2.text

    if 'Falha' in html or 'não retornou sucesso' in html:
        logger.warning('Consulta retornou falha')
        return None

    soup2 = BeautifulSoup(html, 'lxml')

    # Procura link de download
    for a in soup2.find_all('a', href=True):
        href = a['href']
        texto = a.get_text(strip=True).lower()
        if any(x in texto for x in ['baixar', 'download', 'pdf']) or \
           any(x in href.lower() for x in ['.pdf', 'pdf', 'download', 'baixar', 'gerar']):
            pdf_url = href if href.startswith('http') else ANALIZZA_BASE + href
            logger.info(f'PDF encontrado: {pdf_url}')
            r_pdf = session.get(pdf_url, timeout=30)
            logger.info(f'PDF: {len(r_pdf.content)} bytes')
            return base64.b64encode(r_pdf.content).decode('utf-8')

    # Retorna HTML como fallback
    logger.info('PDF não encontrado, retornando HTML')
    return base64.b64encode(html.encode('utf-8')).decode('utf-8')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
