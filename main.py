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
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9',
}

@app.route('/', methods=['GET'])
def health():
    return jsonify({'ok': True, 'mensagem': 'Analizza Bot ativo.'})

@app.route('/diagnostico', methods=['GET'])
def diagnostico():
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        
        # GET na página de login
        r = session.get(ANALIZZA_LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        
        inputs = [{'name': i.get('name'), 'type': i.get('type'), 'id': i.get('id')} 
                  for i in soup.find_all('input')]
        forms = [{'action': f.get('action'), 'method': f.get('method')} 
                 for f in soup.find_all('form')]
        
        return jsonify({
            'ok': True,
            'status': r.status_code,
            'url': r.url,
            'inputs': inputs,
            'forms': forms,
            'html': r.text[:2000]
        })
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

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
        # Cria sessão e faz login
        session = fazer_login()
        
        pdfs = {}
        
        if consultar_scpc:
            logger.info('Consultando SCPC...')
            pdf = consultar_e_baixar(session, ANALIZZA_BASE + URLS[tipo + '_SCPC'], documento, tipo)
            if pdf:
                pdfs['scpc'] = pdf
                
        if consultar_scr:
            logger.info('Consultando SCR...')
            pdf = consultar_e_baixar(session, ANALIZZA_BASE + URLS[tipo + '_SCR'], documento, tipo)
            if pdf:
                pdfs['scr'] = pdf

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
    
    # GET para pegar cookies e tokens
    r = session.get(ANALIZZA_LOGIN_URL, timeout=15)
    logger.info(f'GET login status: {r.status_code}')
    
    soup = BeautifulSoup(r.text, 'lxml')
    
    # Pega campos ocultos
    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        value = inp.get('value', '')
        if name and inp.get('type') == 'hidden':
            payload[name] = value
    
    # Adiciona credenciais
    payload['usuario'] = ANALIZZA_USUARIO
    payload['senha'] = ANALIZZA_SENHA
    
    logger.info(f'Payload login: {list(payload.keys())}')
    
    # Verifica action do form
    form = soup.find('form')
    action = ANALIZZA_LOGIN_URL
    if form and form.get('action'):
        action = form['action']
        if not action.startswith('http'):
            action = ANALIZZA_BASE + action
    
    logger.info(f'Fazendo POST em: {action}')
    
    # POST login
    r2 = session.post(action, data=payload, timeout=15, allow_redirects=True)
    logger.info(f'POST login status: {r2.status_code} | URL final: {r2.url}')
    
    # Verifica se logou
    if 'login' in r2.url.lower() and r2.status_code == 200:
        soup2 = BeautifulSoup(r2.text, 'lxml')
        erro = soup2.find(class_='alert-danger') or soup2.find(class_='error')
        msg_erro = erro.get_text().strip() if erro else 'Redirecionou para login'
        raise Exception(f'Login falhou: {msg_erro}')
    
    logger.info('Login OK!')
    return session

def consultar_e_baixar(session, url, documento, tipo):
    # GET na página de consulta
    r = session.get(url, timeout=15)
    logger.info(f'GET consulta status: {r.status_code}')
    
    soup = BeautifulSoup(r.text, 'lxml')
    
    # Monta payload com campos ocultos
    payload = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        value = inp.get('value', '')
        if name and inp.get('type') == 'hidden':
            payload[name] = value
    
    # Campo do documento
    campo_doc = tipo.lower()
    payload[campo_doc] = documento
    
    logger.info(f'Payload consulta: {list(payload.keys())}')
    
    # POST consulta
    r2 = session.post(url, data=payload, timeout=30)
    logger.info(f'POST consulta status: {r2.status_code}')
    
    if 'Falha' in r2.text or 'não retornou sucesso' in r2.text:
        logger.warning('Consulta retornou falha')
        return None
    
    # Procura link do PDF
    soup2 = BeautifulSoup(r2.text, 'lxml')
    
    pdf_url = None
    for a in soup2.find_all('a', href=True):
        href = a['href']
        texto = a.get_text().lower()
        if 'baixar' in texto or 'download' in texto or '.pdf' in href or 'pdf' in href.lower():
            pdf_url = href
            break
    
    if not pdf_url:
        # Tenta botão
        for btn in soup2.find_all(['button', 'input'], string=lambda t: t and 'baixar' in t.lower()):
            logger.info(f'Botão encontrado: {btn}')
    
    if pdf_url:
        if not pdf_url.startswith('http'):
            pdf_url = ANALIZZA_BASE + pdf_url
        
        r_pdf = session.get(pdf_url, timeout=30)
        logger.info(f'PDF baixado: {len(r_pdf.content)} bytes')
        return base64.b64encode(r_pdf.content).decode('utf-8')
    
    # Fallback: retorna HTML em base64
    logger.info('PDF não encontrado, retornando HTML')
    return base64.b64encode(r2.text.encode('utf-8')).decode('utf-8')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
