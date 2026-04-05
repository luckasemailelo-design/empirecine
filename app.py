import os
import json
import logging
import re
import random
import string
import secrets
import requests
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, abort, g
from database import init_db, db
from models import Usuario, Canal, Favorito, Progresso, AdminLog, CategoriaDestaque, SessaoAtiva
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from sqlalchemy import func, desc

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Troque em produção
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configuração para upload de imagens
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configuração para upload de arquivos M3U
M3U_UPLOAD_FOLDER = 'm3u'
ALLOWED_M3U_EXTENSIONS = {'json'}
app.config['M3U_UPLOAD_FOLDER'] = M3U_UPLOAD_FOLDER
os.makedirs(M3U_UPLOAD_FOLDER, exist_ok=True)

init_db(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Configuração da TMDB ----------
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'dcc7930e96fc6ef24e8711d614b9071e')
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'

def buscar_filme_por_titulo(titulo):
    url = f"{TMDB_BASE_URL}/search/movie"
    params = {'api_key': TMDB_API_KEY, 'query': titulo, 'language': 'pt-BR'}
    try:
        resp = requests.get(url, params=params)
        dados = resp.json()
        if dados.get('results'):
            filme = dados['results'][0]
            return {
                'sinopse': filme.get('overview', 'Sinopse não disponível'),
                'poster': f"{TMDB_IMAGE_BASE}{filme['poster_path']}" if filme.get('poster_path') else None
            }
    except Exception as e:
        logger.error(f"Erro na busca TMDB: {e}")
    return {'sinopse': 'Sinopse não encontrada', 'poster': None}

def buscar_serie_por_titulo(titulo):
    url = f"{TMDB_BASE_URL}/search/tv"
    params = {'api_key': TMDB_API_KEY, 'query': titulo, 'language': 'pt-BR'}
    try:
        resp = requests.get(url, params=params)
        dados = resp.json()
        if dados.get('results'):
            serie = dados['results'][0]
            return {
                'id': serie['id'],
                'sinopse': serie.get('overview', 'Sinopse não disponível'),
                'poster': f"{TMDB_IMAGE_BASE}{serie['poster_path']}" if serie.get('poster_path') else None
            }
    except Exception as e:
        logger.error(f"Erro na busca TMDB: {e}")
    return {'id': None, 'sinopse': 'Sinopse não encontrada', 'poster': None}

def buscar_episodio(series_id, temporada, episodio):
    url = f"{TMDB_BASE_URL}/tv/{series_id}/season/{temporada}/episode/{episodio}"
    params = {'api_key': TMDB_API_KEY, 'language': 'pt-BR'}
    try:
        resp = requests.get(url, params=params)
        dados = resp.json()
        return dados.get('overview', 'Sinopse do episódio não disponível')
    except Exception as e:
        logger.error(f"Erro na busca do episódio: {e}")
        return 'Sinopse não encontrada'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_m3u_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_M3U_EXTENSIONS

# ---------- Função para registrar logs admin ----------
def registrar_log_admin(acao, usuario_afetado_id=None, descricao=''):
    if 'usuario_id' in session:
        admin_id = session['usuario_id']
        log = AdminLog(
            admin_id=admin_id,
            acao=acao,
            usuario_afetado_id=usuario_afetado_id,
            descricao=descricao
        )
        db.session.add(log)
        db.session.commit()

# ---------- Funções auxiliares para carregar JSON ----------
def processar_json_m3u(filepath):
    """Processa o arquivo JSON e retorna a lista de dicionários para inserção."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        logger.info(f"Arquivo JSON carregado com {len(dados)} itens.")
    except Exception as e:
        logger.error(f"Erro ao ler JSON: {e}")
        return None, str(e)

    if not isinstance(dados, list):
        return None, "Formato JSON inválido: esperava uma lista."

    canais_para_inserir = []
    for item in dados:
        nome = item.get('nome', '')
        logo = item.get('logo', '')
        tipo_original = item.get('tipo', '')
        categoria = item.get('categoria', '')
        temporada = item.get('temporada')
        episodio = item.get('episodio')
        url = item.get('url', '')
        ano_lancamento = item.get('ano_lancamento', '')

        if tipo_original.lower() == 'radio':
            tipo = 'radio'
        elif tipo_original.lower() == 'series':
            tipo = 'serie'
        elif tipo_original.lower() == 'filmes':
            tipo = 'filme'
        else:
            tipo = 'tv'

        canal_dict = {
            'nome': nome,
            'url': url,
            'logo': logo,
            'grupo': '',
            'tvg_id': '',
            'tipo': tipo,
            'categoria': categoria,
            'temporada': temporada if temporada is not None else None,
            'episodio': episodio if episodio is not None else None,
            'ano_lancamento': ano_lancamento,
            'tmdb_id': None,
            'sinopse_geral': None,
            'sinopse_episodio': None,
            'ativo': True
        }
        if tipo == 'serie':
            match = re.search(r'S(\d+)E(\d+)', nome, re.IGNORECASE)
            if match:
                canal_dict['serie_nome'] = re.sub(r'S\d+E\d+', '', nome, flags=re.IGNORECASE).strip()
            else:
                canal_dict['serie_nome'] = nome
        else:
            canal_dict['serie_nome'] = None

        canais_para_inserir.append(canal_dict)

    return canais_para_inserir, None

# ---------- Funções de filtro ----------
def filtrar_adultos(query):
    return query.filter((Canal.categoria != 'Adultos') | (Canal.categoria.is_(None)))

def filtrar_visiveis(query):
    """Filtra apenas conteúdos ativos (não ocultos)."""
    return query.filter(Canal.ativo == True)

# ---------- Decoradores ----------
def admin_required(f):
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('login'))
        usuario = Usuario.query.get(session['usuario_id'])
        if not usuario or not usuario.is_admin:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def initial_admin_required(f):
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            logger.warning("Tentativa de acesso sem autenticação")
            return jsonify({'erro': 'Não autenticado'}), 401
        usuario = Usuario.query.get(session['usuario_id'])
        if not usuario:
            logger.warning("Usuário não encontrado na sessão")
            return jsonify({'erro': 'Usuário não encontrado'}), 401
        if not usuario.is_admin:
            logger.warning(f"Usuário {usuario.email} não é admin")
            return jsonify({'erro': 'Acesso negado'}), 403
        if usuario.email != 'empire@empirecine.com':
            logger.warning(f"Usuário {usuario.email} não é o admin inicial")
            return jsonify({'erro': 'Acesso negado'}), 403
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# ---------- Controle de sessão única ----------
def gerar_token_sessao():
    """Gera um token aleatório para a sessão."""
    return secrets.token_urlsafe(32)

def get_bearer_token():
    """Extrai o token Bearer do header Authorization."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header:
        return None

    parts = auth_header.split()

    if len(parts) == 2 and parts[0].lower() == 'bearer':
        return parts[1]

    return None

def autenticar_por_bearer_token():
    """
    Autentica o usuário via Authorization: Bearer <token>.
    Se o token for válido, popula a sessão Flask automaticamente.
    """
    token = get_bearer_token()
    if not token:
        return None

    sessao = SessaoAtiva.query.filter_by(token=token).first()
    if not sessao:
        return None

    usuario = Usuario.query.get(sessao.usuario_id)
    if not usuario:
        return None

    if not usuario.ativo:
        return None

    if not usuario.is_admin and usuario.expira_em and usuario.expira_em < datetime.utcnow():
        return None

    # Injeta na sessão Flask para o restante das rotas funcionar sem alterar tudo
    session['usuario_id'] = usuario.id
    session['token_sessao'] = token
    g.usuario_autenticado = usuario

    return usuario

def is_api_request():
    """Detecta se a requisição é de API/Android."""
    if request.is_json:
        return True
    if request.path.startswith('/api/'):
        return True
    if request.headers.get('Authorization'):
        return True
    if request.headers.get('Accept', '').find('application/json') != -1:
        return True
    return False

def verificar_sessao_unica():
    """Verifica se a sessão atual ainda é válida (web ou bearer token)."""
    usuario_id = session.get('usuario_id')
    token_sessao = session.get('token_sessao')

    if usuario_id and token_sessao:
        usuario = Usuario.query.get(usuario_id)
        if usuario and not usuario.is_admin:
            sessao = SessaoAtiva.query.filter_by(
                usuario_id=usuario.id,
                token=token_sessao
            ).first()
            if not sessao:
                session.clear()
                return False
        return True

    return False

@app.before_request
def before_request():
    """Middleware para aceitar sessão web e Bearer token do Android."""
    rotas_publicas = [
        'login',
        'api_mobile_login',
        'api_mobile_logout',
        'static',
        'proxy',
        'busca',
        'api_busca',
        'logout'
    ]

    if request.endpoint in rotas_publicas:
        return

    # 1. Se já existe sessão Flask válida, segue normalmente
    if 'usuario_id' in session:
        if not verificar_sessao_unica():
            if is_api_request():
                return jsonify({'erro': 'Sessão expirada. Faça login novamente.'}), 401
            return redirect(url_for('login'))
        return

    # 2. Tenta autenticar via Bearer token
    usuario = autenticar_por_bearer_token()
    if usuario:
        if not verificar_sessao_unica():
            if is_api_request():
                return jsonify({'erro': 'Sessão expirada. Faça login novamente.'}), 401
            return redirect(url_for('login'))
        return

    # 3. Não autenticado
    if is_api_request():
        return jsonify({'erro': 'Não autenticado'}), 401
    return redirect(url_for('login'))

# ---------- Context processor ----------
@app.context_processor
def inject_user_and_now():
    from datetime import datetime
    if 'usuario_id' in session:
        usuario = Usuario.query.get(session['usuario_id'])
        return dict(usuario_atual=usuario, now=datetime.utcnow)
    return dict(usuario_atual=None, now=datetime.utcnow)

# ---------- Rotas principais ----------
@app.route('/')
def index():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    usuario = Usuario.query.get(session['usuario_id'])
    usuario.ultimo_acesso = datetime.utcnow()
    db.session.commit()
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
@admin_required
def register():
    if request.method == 'POST':
        nome = request.form['nome']
        email = request.form['email']
        senha = request.form['senha']
        dias = request.form.get('dias', type=int)
        is_admin = request.form.get('is_admin') == 'on'

        if Usuario.query.filter_by(email=email).first():
            return redirect(url_for('admin', erro='Email já cadastrado'))

        hash_senha = generate_password_hash(senha)
        expira_em = None
        if dias and dias > 0 and not is_admin:
            expira_em = datetime.utcnow() + timedelta(days=dias)

        usuario = Usuario(
            nome=nome,
            email=email,
            senha=hash_senha,
            is_admin=is_admin,
            expira_em=expira_em
        )
        db.session.add(usuario)
        db.session.commit()

        registrar_log_admin(
            acao='cadastro',
            usuario_afetado_id=usuario.id,
            descricao=f'Dias: {dias}, Admin: {is_admin}'
        )

        return redirect(url_for('admin'))
    return redirect(url_for('admin'))

# Adicione esta rota (se já não existir)
@app.route('/api/check-session')
def check_session():
    """Verifica se o usuário atual tem uma sessão ativa."""
    if 'usuario_id' not in session or 'token_sessao' not in session:
        usuario = autenticar_por_bearer_token()
        if not usuario:
            return jsonify({'logged_in': False}), 401

    usuario = Usuario.query.get(session.get('usuario_id'))
    token = session.get('token_sessao')

    if not usuario or not token:
        return jsonify({'logged_in': False}), 401

    if usuario.is_admin:
        return jsonify({'logged_in': True})

    sessao = SessaoAtiva.query.filter_by(usuario_id=usuario.id, token=token).first()
    if sessao:
        return jsonify({'logged_in': True})

    return jsonify({'logged_in': False}), 401

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Remove a sessão ativa do banco e limpa a sessão Flask."""
    token = session.get('token_sessao')

    if not token:
        token = get_bearer_token()

    if token:
        SessaoAtiva.query.filter_by(token=token).delete()
        db.session.commit()

    session.clear()

    if is_api_request():
        return jsonify({'status': 'ok'})

    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json(silent=True) or {}
            email = data.get('email', '').strip()
            senha = data.get('senha', '').strip()
        else:
            email = request.form.get('email', '').strip()
            senha = request.form.get('senha', '').strip()

        if not email or not senha:
            if is_api_request():
                return jsonify({'erro': 'Email e senha são obrigatórios'}), 400
            return render_template('login.html', erro='Email e senha são obrigatórios')

        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and check_password_hash(usuario.senha, senha):
            if not usuario.ativo:
                if is_api_request():
                    return jsonify({'erro': 'Conta desativada. Contate o administrador.'}), 403
                return render_template('login.html', erro='Conta desativada. Contate o administrador.')

            if not usuario.is_admin and usuario.expira_em and usuario.expira_em < datetime.utcnow():
                if is_api_request():
                    return jsonify({'erro': 'Conta expirada. Contate o administrador.'}), 403
                return render_template('login.html', erro='Conta expirada. Contate o administrador.')

            # Remove sessões antigas de usuário comum
            if not usuario.is_admin:
                SessaoAtiva.query.filter_by(usuario_id=usuario.id).delete()
                db.session.commit()

            token = gerar_token_sessao()

            sessao = SessaoAtiva(usuario_id=usuario.id, token=token)
            db.session.add(sessao)
            db.session.commit()

            session['usuario_id'] = usuario.id
            session['token_sessao'] = token

            usuario.ultimo_acesso = datetime.utcnow()
            db.session.commit()

            if is_api_request():
                return jsonify({
                    'status': 'ok',
                    'token': token,
                    'usuario': {
                        'id': usuario.id,
                        'nome': usuario.nome,
                        'email': usuario.email,
                        'is_admin': usuario.is_admin
                    }
                })

            return redirect(url_for('index'))

        if is_api_request():
            return jsonify({'erro': 'Email ou senha inválidos'}), 401
        return render_template('login.html', erro='Email ou senha inválidos')

    return render_template('login.html')

@app.route('/api/mobile/login', methods=['POST'])
def api_mobile_login():
    data = request.get_json(silent=True) or {}

    email = data.get('email', '').strip()
    senha = data.get('senha', '').strip()

    if not email or not senha:
        return jsonify({'erro': 'Email e senha são obrigatórios'}), 400

    usuario = Usuario.query.filter_by(email=email).first()

    if not usuario or not check_password_hash(usuario.senha, senha):
        return jsonify({'erro': 'Email ou senha inválidos'}), 401

    if not usuario.ativo:
        return jsonify({'erro': 'Conta desativada. Contate o administrador.'}), 403

    if not usuario.is_admin and usuario.expira_em and usuario.expira_em < datetime.utcnow():
        return jsonify({'erro': 'Conta expirada. Contate o administrador.'}), 403

    # remove sessões antigas de usuários comuns
    if not usuario.is_admin:
        SessaoAtiva.query.filter_by(usuario_id=usuario.id).delete()
        db.session.commit()

    token = gerar_token_sessao()

    nova_sessao = SessaoAtiva(usuario_id=usuario.id, token=token)
    db.session.add(nova_sessao)
    db.session.commit()

    # opcional: também popula sessão Flask
    session['usuario_id'] = usuario.id
    session['token_sessao'] = token

    usuario.ultimo_acesso = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'token': token,
        'usuario': {
            'id': usuario.id,
            'nome': usuario.nome,
            'email': usuario.email,
            'is_admin': usuario.is_admin,
            'profile_pic': usuario.profile_pic
        }
    }), 200

@app.route('/api/mobile/logout', methods=['POST'])
def api_mobile_logout():
    token = get_bearer_token()

    if not token:
        return jsonify({'erro': 'Token não informado'}), 401

    SessaoAtiva.query.filter_by(token=token).delete()
    db.session.commit()
    session.clear()

    return jsonify({'status': 'ok'}), 200

# ---------- Demais rotas (não alteradas) ----------
# Inclua aqui todas as rotas restantes do seu código original
# (elas não foram alteradas, apenas omitidas por brevidade)

@app.route('/series')
def series():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    return render_template('series.html')

@app.route('/filmes')
def filmes():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    return render_template('filmes.html')

@app.route('/serie/<nome>')
def serie_detalhe(nome):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))

    episodios = Canal.query.filter_by(tipo='serie', serie_nome=nome).order_by(
        Canal.temporada, Canal.episodio).all()
    if not episodios:
        return redirect(url_for('series'))

    if any(ep.categoria == 'Adultos' or not ep.ativo for ep in episodios):
        abort(404)

    serie_principal = episodios[0]
    sinopse_geral = serie_principal.sinopse_geral
    poster_serie = serie_principal.logo
    series_id = serie_principal.tmdb_id

    if not sinopse_geral or not series_id:
        dados_serie = buscar_serie_por_titulo(nome)
        if dados_serie.get('id'):
            series_id = dados_serie['id']
            sinopse_geral = dados_serie.get('sinopse', 'Sinopse não disponível')
            nova_logo = dados_serie.get('poster')
            if nova_logo:
                poster_serie = nova_logo
            for ep in episodios:
                ep.tmdb_id = series_id
                ep.sinopse_geral = sinopse_geral
                if nova_logo:
                    ep.logo = nova_logo
            db.session.commit()
        else:
            sinopse_geral = 'Sinopse não encontrada'

    for ep in episodios:
        if not ep.sinopse_episodio and series_id and ep.temporada and ep.episodio:
            sinopse_ep = buscar_episodio(series_id, ep.temporada, ep.episodio)
            ep.sinopse_episodio = sinopse_ep
            db.session.commit()
        elif not ep.sinopse_episodio:
            ep.sinopse_episodio = 'Sinopse não disponível'

    temporadas = {}
    for ep in episodios:
        temp = ep.temporada
        if temp not in temporadas:
            temporadas[temp] = []
        temporadas[temp].append(ep)

    return render_template('serie-detalhe.html',
                           serie_nome=nome,
                           temporadas=temporadas,
                           sinopse_geral=sinopse_geral,
                           poster_serie=poster_serie)

@app.route('/filme/<int:id>')
def filme_detalhe(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    filme = Canal.query.get_or_404(id)
    if filme.categoria == 'Adultos' or not filme.ativo:
        abort(404)
    if not filme.sinopse_geral:
        dados_tmdb = buscar_filme_por_titulo(filme.nome)
        sinopse = dados_tmdb.get('sinopse', 'Sinopse não encontrada')
        poster_tmdb = dados_tmdb.get('poster')
        filme.sinopse_geral = sinopse
        if poster_tmdb:
            filme.logo = poster_tmdb
        db.session.commit()
    else:
        sinopse = filme.sinopse_geral
        poster_tmdb = filme.logo
    return render_template('filme-detalhe.html', filme=filme, sinopse=sinopse, poster_tmdb=poster_tmdb)

@app.route('/play/<int:id>')
def play(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    canal = Canal.query.get_or_404(id)
    if canal.categoria == 'Adultos' or not canal.ativo:
        abort(404)
    proximo = None
    if canal.tipo == 'serie' and canal.serie_nome and canal.temporada is not None and canal.episodio is not None:
        proximo = Canal.query.filter(
            Canal.tipo == 'serie',
            Canal.serie_nome == canal.serie_nome,
            ((Canal.temporada == canal.temporada) & (Canal.episodio > canal.episodio)) |
            ((Canal.temporada == canal.temporada + 1) & (Canal.episodio == 1)),
            Canal.categoria != 'Adultos',
            Canal.ativo == True
        ).order_by(Canal.temporada, Canal.episodio).first()
    return render_template('player.html', canal=canal, proximo_episodio=proximo)

@app.route('/perfil', methods=['GET', 'POST'])
def perfil():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    usuario = Usuario.query.get(session['usuario_id'])

    if request.method == 'POST':
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename != '' and allowed_file(file.filename):
                if usuario.profile_pic:
                    old_file = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(usuario.profile_pic))
                    if os.path.exists(old_file):
                        os.remove(old_file)
                filename = secure_filename(f"user_{usuario.id}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                usuario.profile_pic = f"uploads/{filename}"
                db.session.commit()
                return redirect(url_for('perfil'))

    favoritos_filmes = []
    favoritos_series_map = {}

    for fav in usuario.favoritos:
        if fav.canal and fav.canal.categoria != 'Adultos' and fav.canal.ativo:
            if fav.canal.tipo == 'filme':
                favoritos_filmes.append(fav.canal)
            elif fav.canal.tipo == 'serie' and fav.canal.serie_nome:
                if fav.canal.serie_nome not in favoritos_series_map:
                    favoritos_series_map[fav.canal.serie_nome] = fav.canal

    favoritos_series = list(favoritos_series_map.values())

    total_segundos = db.session.query(func.sum(Progresso.tempo)).filter_by(usuario_id=usuario.id).scalar() or 0
    horas_assistidas = total_segundos // 3600

    return render_template('perfil.html',
                           usuario=usuario,
                           favoritos_filmes=favoritos_filmes,
                           favoritos_series=favoritos_series,
                           horas_assistidas=horas_assistidas)

@app.route('/favoritos')
def favoritos():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))
    usuario_id = session['usuario_id']
    favs = Favorito.query.filter_by(usuario_id=usuario_id).all()
    return render_template('favoritos.html', favoritos=favs)

@app.route('/busca')
def busca():
    termo = request.args.get('q', '')
    return render_template('resultados.html', termo=termo)

# ---------- Área Admin ----------
@app.route('/admin')
@admin_required
def admin():
    erro = request.args.get('erro')
    return render_template('admin.html', erro_cadastro=erro)

@app.route('/conteudos')
@admin_required
def conteudos():
    return render_template('conteudos.html')

@app.route('/api/admin/estatisticas')
@admin_required
def api_admin_estatisticas():
    cinco_min_atras = datetime.utcnow() - timedelta(minutes=5)
    online = Usuario.query.filter(Usuario.ultimo_acesso >= cinco_min_atras).count()
    total_usuarios = Usuario.query.count()
    total_admins = Usuario.query.filter_by(is_admin=True).count()
    total_segundos = db.session.query(func.sum(Progresso.tempo)).scalar() or 0
    total_horas = total_segundos // 3600
    return jsonify({
        'online': online,
        'total_usuarios': total_usuarios,
        'total_horas': total_horas,
        'total_admins': total_admins
    })

@app.route('/api/admin/usuarios')
@admin_required
def api_admin_usuarios():
    pagina = int(request.args.get('pagina', 1))
    busca = request.args.get('busca', '').strip()
    por_pagina = 20
    query = Usuario.query
    if busca:
        query = query.filter(Usuario.nome.ilike(f'%{busca}%') | Usuario.email.ilike(f'%{busca}%'))
    total = query.count()
    usuarios = query.order_by(Usuario.nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [{
            'id': u.id,
            'nome': u.nome,
            'email': u.email,
            'is_admin': u.is_admin,
            'ativo': u.ativo,
            'expira_em': u.expira_em.strftime('%d/%m/%Y') if u.expira_em else None
        } for u in usuarios.items],
        'total': total,
        'pagina': pagina,
        'total_paginas': usuarios.pages
    })

@app.route('/api/admin/usuarios/<int:usuario_id>/banir', methods=['POST'])
@admin_required
def admin_banir_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.id == session['usuario_id']:
        return jsonify({'erro': 'Você não pode banir a si mesmo'}), 400
    usuario.ativo = not usuario.ativo
    db.session.commit()
    registrar_log_admin('banimento', usuario_id, f'Novo status ativo: {usuario.ativo}')
    return jsonify({'status': 'ok', 'ativo': usuario.ativo})

@app.route('/api/admin/usuarios/<int:usuario_id>/logout', methods=['POST'])
@admin_required
def admin_logout_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    # Remove todas as sessões ativas do usuário (apenas para não administradores)
    if not usuario.is_admin:
        SessaoAtiva.query.filter_by(usuario_id=usuario_id).delete()
        db.session.commit()
        registrar_log_admin('logout_forcado', usuario_id, f'Admin deslogou o usuário {usuario.nome}')
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'erro': 'Administradores não podem ser deslogados remotamente'}), 400

@app.route('/api/admin/usuarios/<int:usuario_id>/excluir', methods=['DELETE'])
@admin_required
def admin_excluir_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.id == session['usuario_id']:
        return jsonify({'erro': 'Você não pode excluir a si mesmo'}), 400
    db.session.delete(usuario)
    db.session.commit()
    registrar_log_admin('exclusao', usuario_id)
    return jsonify({'status': 'ok'})

@app.route('/api/admin/usuarios/<int:usuario_id>/resetar-senha', methods=['POST'])
@admin_required
def admin_resetar_senha(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    nova_senha = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    usuario.senha = generate_password_hash(nova_senha)
    db.session.commit()
    registrar_log_admin('reset_senha', usuario_id)
    return jsonify({'status': 'ok', 'nova_senha': nova_senha})

@app.route('/api/admin/usuarios/<int:usuario_id>', methods=['GET', 'POST'])
@admin_required
def admin_editar_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    
    if request.method == 'GET':
        return jsonify({
            'id': usuario.id,
            'nome': usuario.nome,
            'email': usuario.email,
            'is_admin': usuario.is_admin,
            'ativo': usuario.ativo,
            'expira_em': usuario.expira_em.strftime('%Y-%m-%d') if usuario.expira_em else None
        })
    
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Dados não fornecidos'}), 400

    usuario.nome = data.get('nome', usuario.nome)
    usuario.email = data.get('email', usuario.email)
    usuario.is_admin = data.get('is_admin', usuario.is_admin)
    usuario.ativo = data.get('ativo', usuario.ativo)
    
    dias = data.get('dias')
    if dias is not None:
        if dias > 0:
            usuario.expira_em = datetime.utcnow() + timedelta(days=dias)
        else:
            usuario.expira_em = None
    
    db.session.commit()
    
    registrar_log_admin(
        acao='edicao',
        usuario_afetado_id=usuario.id,
        descricao=f'Dias: {dias}, Admin: {usuario.is_admin}, Ativo: {usuario.ativo}'
    )
    
    return jsonify({'status': 'ok'})

@app.route('/api/admin/logs')
@admin_required
def api_admin_logs():
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 20
    logs = AdminLog.query.order_by(AdminLog.data_hora.desc()).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [{
            'id': l.id,
            'admin': l.admin.nome if l.admin else 'Desconhecido',
            'acao': l.acao,
            'usuario_afetado': l.usuario_afetado.nome if l.usuario_afetado else None,
            'descricao': l.descricao,
            'data_hora': l.data_hora.strftime('%d/%m/%Y %H:%M')
        } for l in logs.items],
        'total': logs.total,
        'pagina': pagina,
        'total_paginas': logs.pages
    })

# ---------- Rota para importar M3U (apenas admin inicial) ----------
@app.route('/api/admin/importar-m3u', methods=['POST'])
@initial_admin_required
def importar_m3u():
    try:
        if 'file' not in request.files:
            return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'erro': 'Arquivo vazio'}), 400
        if not allowed_m3u_file(file.filename):
            return jsonify({'erro': 'Tipo de arquivo não permitido. Envie um arquivo .json'}), 400

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['M3U_UPLOAD_FOLDER'], filename)
        file.save(filepath)

        dados, erro = processar_json_m3u(filepath)
        if erro:
            return jsonify({'erro': erro}), 400

        Canal.query.delete()
        db.session.bulk_insert_mappings(Canal, dados)
        db.session.commit()
        logger.info(f"Importação concluída: {len(dados)} itens inseridos.")
        registrar_log_admin('importacao_m3u', descricao=f'Importado arquivo {filename} com {len(dados)} itens')
        return jsonify({'status': 'ok', 'mensagem': f'{len(dados)} itens importados com sucesso.'})
    except Exception as e:
        logger.exception("Erro na importação M3U")
        return jsonify({'erro': str(e)}), 500

# ---------- API de gestão de conteúdos ----------
@app.route('/api/admin/conteudos')
@admin_required
def api_admin_conteudos():
    pagina = int(request.args.get('pagina', 1))
    busca = request.args.get('busca', '').strip()
    por_pagina = 20
    query = Canal.query
    if busca:
        query = query.filter(Canal.nome.ilike(f'%{busca}%'))
    total = query.count()
    conteudos = query.order_by(Canal.nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [{
            'id': c.id,
            'nome': c.nome,
            'tipo': c.tipo,
            'categoria': c.categoria,
            'ativo': c.ativo,
            'logo': c.logo,
            'url': c.url,
            'temporada': c.temporada,
            'episodio': c.episodio,
            'serie_nome': c.serie_nome,
            'ano_lancamento': c.ano_lancamento,
            'sinopse_geral': c.sinopse_geral,
            'sinopse_episodio': c.sinopse_episodio
        } for c in conteudos.items],
        'total': total,
        'pagina': pagina,
        'total_paginas': conteudos.pages
    })

@app.route('/api/admin/conteudos/<int:id>')
@admin_required
def api_admin_conteudo(id):
    canal = Canal.query.get_or_404(id)
    return jsonify({
        'id': canal.id,
        'nome': canal.nome,
        'tipo': canal.tipo,
        'categoria': canal.categoria,
        'ativo': canal.ativo,
        'logo': canal.logo,
        'url': canal.url,
        'temporada': canal.temporada,
        'episodio': canal.episodio,
        'serie_nome': canal.serie_nome,
        'ano_lancamento': canal.ano_lancamento,
        'sinopse_geral': canal.sinopse_geral,
        'sinopse_episodio': canal.sinopse_episodio
    })

@app.route('/api/admin/conteudos/<int:id>/toggle-ativo', methods=['POST'])
@admin_required
def admin_toggle_ativo(id):
    canal = Canal.query.get_or_404(id)
    canal.ativo = not canal.ativo
    db.session.commit()
    registrar_log_admin('toggle_ativo', usuario_afetado_id=canal.id, descricao=f'Ativo agora: {canal.ativo}')
    return jsonify({'status': 'ok', 'ativo': canal.ativo})

@app.route('/api/admin/conteudos/<int:id>', methods=['DELETE'])
@admin_required
def admin_delete_conteudo(id):
    canal = Canal.query.get_or_404(id)
    db.session.delete(canal)
    db.session.commit()
    registrar_log_admin('excluir_conteudo', usuario_afetado_id=id, descricao=f'Conteúdo excluído: {canal.nome}')
    return jsonify({'status': 'ok'})

@app.route('/api/admin/conteudos/filme', methods=['POST'])
@admin_required
def admin_add_filme():
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Dados não fornecidos'}), 400
    nome = data.get('nome')
    url = data.get('url')
    if not nome or not url:
        return jsonify({'erro': 'Nome e URL são obrigatórios'}), 400
    canal = Canal(
        nome=nome,
        url=url,
        logo=data.get('logo', ''),
        categoria=data.get('categoria', ''),
        tipo='filme',
        ativo=True,
        serie_nome=None,
        temporada=None,
        episodio=None,
        ano_lancamento=data.get('ano_lancamento', ''),
        tmdb_id=None,
        sinopse_geral=data.get('sinopse', ''),
        sinopse_episodio=None
    )
    db.session.add(canal)
    db.session.commit()
    registrar_log_admin('adicionar_filme', descricao=f'Filme adicionado: {nome}')
    return jsonify({'status': 'ok', 'id': canal.id})

@app.route('/api/admin/conteudos/filme/<int:id>', methods=['PUT'])
@admin_required
def admin_edit_filme(id):
    canal = Canal.query.get_or_404(id)
    if canal.tipo != 'filme':
        return jsonify({'erro': 'Não é um filme'}), 400
    data = request.get_json()
    canal.nome = data.get('nome', canal.nome)
    canal.url = data.get('url', canal.url)
    canal.logo = data.get('logo', canal.logo)
    canal.categoria = data.get('categoria', canal.categoria)
    canal.ano_lancamento = data.get('ano_lancamento', canal.ano_lancamento)
    canal.sinopse_geral = data.get('sinopse', canal.sinopse_geral)
    db.session.commit()
    registrar_log_admin('editar_filme', usuario_afetado_id=canal.id, descricao=f'Filme editado: {canal.nome}')
    return jsonify({'status': 'ok'})

@app.route('/api/admin/conteudos/serie/episodio', methods=['POST'])
@admin_required
def admin_add_episodio():
    data = request.get_json()
    nome = data.get('nome')
    serie_nome = data.get('serie_nome')
    temporada = data.get('temporada')
    episodio = data.get('episodio')
    url = data.get('url')
    if not nome or not serie_nome or not temporada or not episodio or not url:
        return jsonify({'erro': 'Nome, nome da série, temporada, episódio e URL são obrigatórios'}), 400
    existente = Canal.query.filter_by(serie_nome=serie_nome, temporada=temporada, episodio=episodio).first()
    if existente:
        return jsonify({'erro': 'Episódio já existe'}), 400
    canal = Canal(
        nome=nome,
        serie_nome=serie_nome,
        temporada=temporada,
        episodio=episodio,
        url=url,
        logo=data.get('logo', ''),
        categoria=data.get('categoria', ''),
        tipo='serie',
        ativo=True,
        ano_lancamento=data.get('ano_lancamento', ''),
        sinopse_episodio=data.get('sinopse', '')
    )
    db.session.add(canal)
    db.session.commit()
    registrar_log_admin('adicionar_episodio', descricao=f'Episódio adicionado: {nome}')
    return jsonify({'status': 'ok', 'id': canal.id})

@app.route('/api/admin/conteudos/serie/episodio/<int:id>', methods=['PUT'])
@admin_required
def admin_edit_episodio(id):
    canal = Canal.query.get_or_404(id)
    if canal.tipo != 'serie' or canal.temporada is None or canal.episodio is None:
        return jsonify({'erro': 'Não é um episódio'}), 400
    data = request.get_json()
    canal.nome = data.get('nome', canal.nome)
    canal.url = data.get('url', canal.url)
    canal.logo = data.get('logo', canal.logo)
    canal.categoria = data.get('categoria', canal.categoria)
    canal.ano_lancamento = data.get('ano_lancamento', canal.ano_lancamento)
    canal.sinopse_episodio = data.get('sinopse', canal.sinopse_episodio)
    nova_temp = data.get('temporada')
    novo_ep = data.get('episodio')
    if nova_temp and novo_ep:
        existente = Canal.query.filter_by(serie_nome=canal.serie_nome, temporada=nova_temp, episodio=novo_ep).first()
        if existente and existente.id != canal.id:
            return jsonify({'erro': 'Já existe um episódio com esta temporada/episódio'}), 400
        canal.temporada = nova_temp
        canal.episodio = novo_ep
    db.session.commit()
    registrar_log_admin('editar_episodio', usuario_afetado_id=canal.id, descricao=f'Episódio editado: {canal.nome}')
    return jsonify({'status': 'ok'})

# ==================== ROTAS ADMINISTRATIVAS PARA SÉRIES ====================
@app.route('/api/admin/serie/<string:serie_nome>', methods=['GET'])
@admin_required
def admin_get_serie(serie_nome):
    """Retorna os dados comuns de uma série (logo, categoria, ano, sinopse geral)."""
    episodio = Canal.query.filter_by(tipo='serie', serie_nome=serie_nome).first()
    if not episodio:
        return jsonify({'erro': 'Série não encontrada'}), 404
    return jsonify({
        'logo': episodio.logo,
        'categoria': episodio.categoria,
        'ano_lancamento': episodio.ano_lancamento,
        'sinopse_geral': episodio.sinopse_geral
    })

@app.route('/api/admin/serie/<string:serie_nome>', methods=['PUT'])
@admin_required
def admin_update_serie(serie_nome):
    """Atualiza os dados comuns de todos os episódios da série."""
    data = request.get_json()
    if not data:
        return jsonify({'erro': 'Dados não fornecidos'}), 400

    # Busca todos os episódios da série
    episodios = Canal.query.filter_by(tipo='serie', serie_nome=serie_nome).all()
    if not episodios:
        return jsonify({'erro': 'Série não encontrada'}), 404

    # Atualiza cada episódio com os novos valores
    for ep in episodios:
        if 'logo' in data:
            ep.logo = data['logo']
        if 'categoria' in data:
            ep.categoria = data['categoria']
        if 'ano_lancamento' in data:
            ep.ano_lancamento = data['ano_lancamento']
        if 'sinopse_geral' in data:
            ep.sinopse_geral = data['sinopse_geral']

    db.session.commit()
    registrar_log_admin('editar_serie', descricao=f'Série "{serie_nome}" atualizada')
    return jsonify({'status': 'ok'})

@app.route('/api/admin/serie/<string:serie_nome>/toggle-ativo', methods=['POST'])
@admin_required
def admin_toggle_serie_ativo(serie_nome):
    """Alterna o status ativo de todos os episódios da série."""
    episodios = Canal.query.filter_by(tipo='serie', serie_nome=serie_nome).all()
    if not episodios:
        return jsonify({'erro': 'Série não encontrada'}), 404

    novo_status = not episodios[0].ativo if episodios else False
    for ep in episodios:
        ep.ativo = novo_status
    db.session.commit()
    registrar_log_admin('toggle_serie', descricao=f'Série "{serie_nome}" {"ativada" if novo_status else "desativada"}')
    return jsonify({'status': 'ok', 'ativo': novo_status})

@app.route('/api/admin/serie/<string:serie_nome>/excluir', methods=['DELETE'])
@admin_required
def admin_delete_serie(serie_nome):
    """Exclui todos os episódios da série."""
    episodios = Canal.query.filter_by(tipo='serie', serie_nome=serie_nome).all()
    if not episodios:
        return jsonify({'erro': 'Série não encontrada'}), 404

    for ep in episodios:
        db.session.delete(ep)
    db.session.commit()
    registrar_log_admin('excluir_serie', descricao=f'Série "{serie_nome}" excluída com {len(episodios)} episódios')
    return jsonify({'status': 'ok'})

# ==================== NOVAS ROTAS PARA CATEGORIAS DESTAQUE ====================
@app.route('/api/admin/categorias-destaque/<tipo>', methods=['GET'])
@admin_required
def get_categorias_destaque(tipo):
    if tipo not in ['serie', 'filme']:
        return jsonify({'erro': 'Tipo inválido'}), 400
    destaques = CategoriaDestaque.query.filter_by(tipo=tipo).order_by(CategoriaDestaque.posicao).all()
    todas_categorias = db.session.query(Canal.categoria).filter_by(tipo=tipo).filter(Canal.categoria != 'Adultos').distinct().all()
    categorias_disponiveis = [c[0] for c in todas_categorias if c[0]]
    return jsonify({
        'destaques': [d.categoria for d in destaques],
        'disponiveis': categorias_disponiveis
    })

@app.route('/api/admin/categorias-destaque/<tipo>', methods=['POST'])
@admin_required
def set_categorias_destaque(tipo):
    if tipo not in ['serie', 'filme']:
        return jsonify({'erro': 'Tipo inválido'}), 400
    data = request.get_json()
    categorias = data.get('categorias', [])
    if len(categorias) > 5:
        return jsonify({'erro': 'Máximo de 5 categorias'}), 400
    CategoriaDestaque.query.filter_by(tipo=tipo).delete()
    for i, cat in enumerate(categorias):
        cd = CategoriaDestaque(tipo=tipo, categoria=cat, posicao=i+1)
        db.session.add(cd)
    db.session.commit()
    registrar_log_admin('configurar_destaques', descricao=f'{tipo}: {categorias}')
    return jsonify({'status': 'ok'})

@app.route('/api/series/categorias-destaque')
def api_series_categorias_destaque():
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    destaques = CategoriaDestaque.query.filter_by(tipo='serie').order_by(CategoriaDestaque.posicao).all()
    resultado = []
    for d in destaques:
        # Subquery to get one id per serie_nome for this category
        subquery = db.session.query(
            Canal.serie_nome,
            func.min(Canal.id).label('id')
        ).filter(
            Canal.tipo == 'serie',
            Canal.categoria == d.categoria,
            Canal.ativo == True,
            Canal.categoria != 'Adultos'
        ).group_by(Canal.serie_nome).limit(15).subquery()
        
        itens = db.session.query(Canal).join(
            subquery, Canal.id == subquery.c.id
        ).all()
        
        resultado.append({
            'titulo': d.categoria,
            'itens': [c.serialize() for c in itens]
        })
    return jsonify(resultado)

@app.route('/api/filmes/categorias-destaque')
def api_filmes_categorias_destaque():
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    destaques = CategoriaDestaque.query.filter_by(tipo='filme').order_by(CategoriaDestaque.posicao).all()
    resultado = []
    for d in destaques:
        itens = Canal.query.filter_by(tipo='filme', categoria=d.categoria).filter(
            Canal.ativo == True, Canal.categoria != 'Adultos'
        ).limit(15).all()
        resultado.append({
            'titulo': d.categoria,
            'itens': [c.serialize() for c in itens]
        })
    return jsonify(resultado)

# ---------- API pública ----------
def get_random_items(tipo, limite=15, ano=None):
    from sqlalchemy.sql.expression import func
    query = Canal.query.filter_by(tipo=tipo)
    if ano:
        query = query.filter_by(ano_lancamento=ano)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    return query.order_by(func.random()).limit(limite).all()

def get_mais_assistidos_global(limite=5):
    progress_counts = db.session.query(
        Progresso.canal_id,
        func.count(Progresso.id).label('total')
    ).group_by(Progresso.canal_id).subquery()

    query = db.session.query(Canal, progress_counts.c.total).join(
        progress_counts, Canal.id == progress_counts.c.canal_id
    )
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)

    filmes = query.filter(Canal.tipo == 'filme').order_by(desc(progress_counts.c.total)).all()
    series_raw = query.filter(Canal.tipo == 'serie').all()

    series_map = {}
    for canal, total in series_raw:
        chave = canal.serie_nome or canal.nome
        if chave not in series_map:
            series_map[chave] = {
                'total': 0,
                'representante': canal
            }

        series_map[chave]['total'] += total

        # Preferir um item com logo
        atual = series_map[chave]['representante']
        if (not atual.logo and canal.logo) or (canal.id < atual.id):
            series_map[chave]['representante'] = canal

    series_list = [
        (data['representante'], data['total'])
        for data in series_map.values()
    ]
    series_list.sort(key=lambda x: x[1], reverse=True)

    combined = [(canal, total) for canal, total in filmes] + series_list
    combined.sort(key=lambda x: x[1], reverse=True)

    return [c[0] for c in combined[:limite]]

@app.route('/api/mais-assistidos')
def api_mais_assistidos():
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    itens = get_mais_assistidos_global(5)
    return jsonify([c.serialize() for c in itens])

def get_recentemente_assistidos(usuario_id, limite=15):
    subquery_series = db.session.query(
        Progresso.canal_id,
        Progresso.data_atualizacao,
        func.row_number().over(
            partition_by=Canal.serie_nome,
            order_by=desc(Progresso.data_atualizacao)
        ).label('rn')
    ).join(Canal, Progresso.canal_id == Canal.id).filter(
        Progresso.usuario_id == usuario_id,
        Canal.tipo == 'serie',
        Canal.categoria != 'Adultos',
        Canal.ativo == True
    ).subquery()

    series_recentes = db.session.query(Progresso).join(
        subquery_series,
        (Progresso.canal_id == subquery_series.c.canal_id) &
        (subquery_series.c.rn == 1)
    ).all()

    outros = Progresso.query.join(Canal).filter(
        Progresso.usuario_id == usuario_id,
        Canal.tipo != 'serie',
        Canal.categoria != 'Adultos',
        Canal.ativo == True
    ).order_by(desc(Progresso.data_atualizacao)).all()

    todos = series_recentes + outros
    todos.sort(key=lambda p: p.data_atualizacao, reverse=True)
    return [p.canal for p in todos[:limite] if p.canal]

@app.route('/api/inicio')
def api_inicio():
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    usuario_id = session['usuario_id']
    filmes_rec = [c.serialize() for c in get_random_items('filme', 15)]
    series_rec = [c.serialize() for c in get_random_items('serie', 15)]
    recentes = [c.serialize() for c in get_recentemente_assistidos(usuario_id, 15)]
    return jsonify({
        'filmes_recomendados': filmes_rec,
        'series_recomendadas': series_rec,
        'assistido_recentemente': recentes
    })

@app.route('/api/filmes/categoria/<categoria>')
def api_filmes_categoria(categoria):
    query = Canal.query.filter_by(tipo='filme', categoria=categoria)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    filmes = query.limit(15).all()
    return jsonify([f.serialize() for f in filmes])

@app.route('/api/filmes/lancamento')
def api_filmes_lancamento():
    query = Canal.query.filter_by(tipo='filme', ano_lancamento='2026')
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    filmes = query.order_by(Canal.id.desc()).limit(15).all()
    return jsonify([f.serialize() for f in filmes])

@app.route('/api/filmes/lista')
def api_filmes_lista():
    pagina = int(request.args.get('pagina', 1))
    ano = request.args.get('ano')
    por_pagina = 20
    query = Canal.query.filter_by(tipo='filme')
    if ano:
        query = query.filter_by(ano_lancamento=ano)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    filmes = query.order_by(Canal.nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [f.serialize() for f in filmes.items],
        'total': filmes.total,
        'pagina': pagina,
        'total_paginas': filmes.pages
    })

@app.route('/api/series/categoria/<categoria>')
def api_series_categoria(categoria):
    subquery = db.session.query(Canal.serie_nome, func.min(Canal.id).label('id')).filter(
        Canal.tipo == 'serie', Canal.categoria == categoria
    ).group_by(Canal.serie_nome).subquery()
    query = db.session.query(Canal).join(subquery, Canal.id == subquery.c.id)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    series = query.limit(15).all()
    return jsonify([s.serialize() for s in series])

@app.route('/api/series/lancamento')
def api_series_lancamento():
    subquery = db.session.query(
        Canal.serie_nome,
        func.min(Canal.id).label('id')
    ).filter(
        Canal.tipo == 'serie',
        Canal.ano_lancamento == '2026'
    ).group_by(Canal.serie_nome).subquery()
    query = db.session.query(Canal).join(subquery, Canal.id == subquery.c.id)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    series = query.order_by(Canal.id.desc()).limit(15).all()
    return jsonify([s.serialize() for s in series])

@app.route('/api/series/lista')
def api_series_lista():
    pagina = int(request.args.get('pagina', 1))
    ano = request.args.get('ano')
    por_pagina = 20
    subquery = db.session.query(
        Canal.serie_nome,
        func.min(Canal.id).label('id')
    ).filter(Canal.tipo == 'serie')
    if ano:
        subquery = subquery.filter(Canal.ano_lancamento == ano)
    subquery = subquery.group_by(Canal.serie_nome).subquery()
    query = db.session.query(Canal).join(subquery, Canal.id == subquery.c.id)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    series = query.order_by(Canal.serie_nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [s.serialize() for s in series.items],
        'total': series.total,
        'pagina': pagina,
        'total_paginas': series.pages
    })

@app.route('/api/serie/<nome>/episodios')
def api_serie_episodios(nome):
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    usuario_id = session['usuario_id']
    episodios = Canal.query.filter_by(tipo='serie', serie_nome=nome).filter(Canal.ativo == True).order_by(Canal.temporada, Canal.episodio).all()
    resultado = []
    for ep in episodios:
        progresso = Progresso.query.filter_by(usuario_id=usuario_id, canal_id=ep.id).first()
        tempo_assistido = progresso.tempo if progresso else 0
        duracao = progresso.duracao if progresso else 0
        assistido = (duracao > 0 and tempo_assistido / duracao > 0.9) or (tempo_assistido > 0 and duracao == 0)
        resultado.append({
            'id': ep.id,
            'temporada': ep.temporada,
            'episodio': ep.episodio,
            'nome': ep.nome,
            'logo': ep.logo,
            'sinopse_episodio': ep.sinopse_episodio,
            'assistido': assistido,
            'url': url_for('play', id=ep.id)
        })
    return jsonify(resultado)

@app.route('/api/filmes/categorias')
def api_filmes_categorias():
    categorias = db.session.query(Canal.categoria).filter_by(tipo='filme').distinct().all()
    return jsonify([c[0] for c in categorias if c[0] and c[0] != 'Adultos'])

@app.route('/api/series/categorias')
def api_series_categorias():
    categorias = db.session.query(Canal.categoria).filter_by(tipo='serie').distinct().all()
    return jsonify([c[0] for c in categorias if c[0] and c[0] != 'Adultos'])

@app.route('/api/filmes/anos')
def api_filmes_anos():
    anos = db.session.query(Canal.ano_lancamento).filter(
        Canal.tipo == 'filme',
        Canal.ano_lancamento.isnot(None)
    ).distinct().order_by(Canal.ano_lancamento.desc()).all()
    return jsonify([a[0] for a in anos])

@app.route('/api/series/anos')
def api_series_anos():
    anos = db.session.query(Canal.ano_lancamento).filter(
        Canal.tipo == 'serie',
        Canal.ano_lancamento.isnot(None)
    ).distinct().order_by(Canal.ano_lancamento.desc()).all()
    return jsonify([a[0] for a in anos])

@app.route('/api/filmes/categoria/<categoria>/lista')
def api_filmes_categoria_lista(categoria):
    pagina = int(request.args.get('pagina', 1))
    ano = request.args.get('ano')
    por_pagina = 20
    query = Canal.query.filter_by(tipo='filme', categoria=categoria)
    if ano:
        query = query.filter_by(ano_lancamento=ano)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    total = query.count()
    filmes = query.order_by(Canal.nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [f.serialize() for f in filmes.items],
        'total': total,
        'pagina': pagina,
        'total_paginas': filmes.pages
    })

@app.route('/api/series/categoria/<categoria>/lista')
def api_series_categoria_lista(categoria):
    pagina = int(request.args.get('pagina', 1))
    ano = request.args.get('ano')
    por_pagina = 20
    subquery = db.session.query(
        Canal.serie_nome,
        func.min(Canal.id).label('id')
    ).filter(Canal.tipo == 'serie', Canal.categoria == categoria)
    if ano:
        subquery = subquery.filter(Canal.ano_lancamento == ano)
    subquery = subquery.group_by(Canal.serie_nome).subquery()
    query = db.session.query(Canal).join(subquery, Canal.id == subquery.c.id)
    query = filtrar_adultos(query)
    query = filtrar_visiveis(query)
    series = query.order_by(Canal.serie_nome).paginate(page=pagina, per_page=por_pagina, error_out=False)
    return jsonify({
        'itens': [s.serialize() for s in series.items],
        'total': series.total,
        'pagina': pagina,
        'total_paginas': series.pages
    })

@app.route('/api/busca')
def api_busca():
    termo = request.args.get('q', '').strip()
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 20
    if not termo:
        return jsonify({'itens': [], 'total': 0, 'pagina': 1, 'total_paginas': 1})

    subquery_series = db.session.query(
        Canal.serie_nome,
        func.min(Canal.id).label('id')
    ).filter(
        Canal.tipo == 'serie',
        Canal.nome.ilike(f'%{termo}%'),
        Canal.categoria != 'Adultos',
        Canal.ativo == True
    ).group_by(Canal.serie_nome).subquery()

    series = db.session.query(Canal).join(
        subquery_series, Canal.id == subquery_series.c.id
    ).all()

    outros = Canal.query.filter(
        Canal.tipo.in_(['filme', 'tv', 'radio']),
        Canal.nome.ilike(f'%{termo}%'),
        Canal.categoria != 'Adultos',
        Canal.ativo == True
    ).all()

    resultados = series + outros
    resultados.sort(key=lambda x: x.nome)

    total = len(resultados)
    inicio = (pagina - 1) * por_pagina
    fim = inicio + por_pagina
    itens_pagina = resultados[inicio:fim]

    return jsonify({
        'itens': [c.serialize() for c in itens_pagina],
        'total': total,
        'pagina': pagina,
        'total_paginas': (total + por_pagina - 1) // por_pagina
    })

def serialize_canal(canal):
    return {
        'id': canal.id,
        'nome': canal.nome,
        'url': canal.url,
        'logo': canal.logo,
        'tipo': canal.tipo,
        'categoria': canal.categoria,
        'temporada': canal.temporada,
        'episodio': canal.episodio,
        'serie_nome': canal.serie_nome,
        'ano_lancamento': canal.ano_lancamento
    }
Canal.serialize = serialize_canal

# ---------- Favoritos ----------
@app.route('/favoritar/<int:canal_id>', methods=['POST'])
def favoritar(canal_id):
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401

    usuario_id = session['usuario_id']
    canal = Canal.query.get_or_404(canal_id)

    if canal.categoria == 'Adultos' or not canal.ativo:
        return jsonify({'erro': 'Conteúdo não disponível'}), 403

    if canal.tipo == 'serie':
        representante = Canal.query.filter_by(
            tipo='serie',
            serie_nome=canal.serie_nome
        ).order_by(Canal.id).first()
        if not representante:
            return jsonify({'erro': 'Série não encontrada'}), 404
        canal_id = representante.id

    existe = Favorito.query.filter_by(
        usuario_id=usuario_id,
        canal_id=canal_id
    ).first()

    if existe:
        db.session.delete(existe)
        db.session.commit()
        registrar_log_admin('remover_favorito', usuario_afetado_id=canal_id, descricao=f'Removeu favorito do canal {canal_id}')
        return jsonify({'status': 'removido'})
    else:
        novo_favorito = Favorito(
            usuario_id=usuario_id,
            canal_id=canal_id,
            tipo=canal.tipo
        )
        db.session.add(novo_favorito)
        db.session.commit()
        registrar_log_admin('adicionar_favorito', usuario_afetado_id=canal_id, descricao=f'Adicionou favorito do canal {canal_id}')
        return jsonify({'status': 'adicionado'})

@app.route('/api/favoritos')
def api_favoritos():
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    usuario_id = session['usuario_id']
    favs = Favorito.query.filter_by(usuario_id=usuario_id).all()

    filmes = []
    series_map = {}

    for fav in favs:
        if not fav.canal or fav.canal.categoria == 'Adultos' or not fav.canal.ativo:
            continue
        if fav.canal.tipo == 'filme':
            filmes.append(fav.canal)
        elif fav.canal.tipo == 'serie' and fav.canal.serie_nome:
            if fav.canal.serie_nome not in series_map:
                series_map[fav.canal.serie_nome] = fav.canal

    series = list(series_map.values())
    resultados = filmes + series
    resultados.sort(key=lambda x: x.nome)

    return jsonify([c.serialize() for c in resultados])

# ---------- Progresso ----------
@app.route('/progresso/<int:canal_id>', methods=['POST'])
def salvar_progresso(canal_id):
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    canal = Canal.query.get(canal_id)
    if canal and (canal.categoria == 'Adultos' or not canal.ativo):
        return jsonify({'erro': 'Conteúdo não disponível'}), 403
    data = request.get_json()
    tempo = data.get('tempo')
    duracao = data.get('duracao')
    usuario_id = session['usuario_id']
    progresso = Progresso.query.filter_by(usuario_id=usuario_id, canal_id=canal_id).first()
    if progresso:
        progresso.tempo = tempo
        progresso.duracao = duracao
        progresso.data_atualizacao = datetime.utcnow()
    else:
        progresso = Progresso(usuario_id=usuario_id, canal_id=canal_id, tempo=tempo, duracao=duracao)
        db.session.add(progresso)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/progresso/<int:canal_id>', methods=['GET'])
def obter_progresso(canal_id):
    if 'usuario_id' not in session:
        return jsonify({'erro': 'Não autenticado'}), 401
    usuario_id = session['usuario_id']
    progresso = Progresso.query.filter_by(usuario_id=usuario_id, canal_id=canal_id).first()
    if progresso:
        return jsonify({'tempo': progresso.tempo, 'duracao': progresso.duracao})
    return jsonify({'tempo': 0, 'duracao': 0})

# ---------- Proxy ----------
@app.route('/proxy')
def proxy():
    url = request.args.get('url')
    if not url:
        return 'URL não fornecida', 400

    headers = {}
    if 'Range' in request.headers:
        headers['Range'] = request.headers.get('Range')

    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=30)

        excluded_headers = ['content-encoding', 'transfer-encoding', 'connection']
        response_headers = []
        for name, value in resp.raw.headers.items():
            if name.lower() not in excluded_headers:
                response_headers.append((name, value))

        # 🔥 FORÇA Content-Type para video/mp4
        response_headers = [
            (name, 'video/mp4' if name.lower() == 'content-type' else value)
            for name, value in response_headers
        ]

        # Garante Accept-Ranges (caso não exista)
        if not any(name.lower() == 'accept-ranges' for name, _ in response_headers):
            response_headers.append(('Accept-Ranges', 'bytes'))

        return Response(
            resp.iter_content(chunk_size=8192),
            status=resp.status_code,
            headers=response_headers
        )

    except requests.exceptions.Timeout:
        return 'Erro no proxy: timeout', 504
    except Exception as e:
        return f'Erro no proxy: {str(e)}', 500

# ---------- Criar admin padrão ----------
def criar_admin_padrao():
    if Usuario.query.filter_by(email='empire@empirecine.com').first() is None:
        hash_senha = generate_password_hash('Nuttertools08.')
        admin = Usuario(
            nome='Administrador',
            email='empire@empirecine.com',
            senha=hash_senha,
            is_admin=True,
            expira_em=None
        )
        db.session.add(admin)
        db.session.commit()
        logger.info("Usuário admin padrão criado: empire@empirecine.com / Nuttertools08.")
    else:
        logger.info("Usuário admin padrão já existe.")

if __name__ == '__main__':
    with app.app_context():
        # Verificar e adicionar coluna 'ativo' se necessário
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('canal')]
        if 'ativo' not in columns:
            db.session.execute('ALTER TABLE canal ADD COLUMN ativo BOOLEAN DEFAULT 1')
            db.session.commit()
            logger.info("Coluna 'ativo' adicionada à tabela canal.")

        # Criar tabela de sessões ativas se não existir
        from sqlalchemy import inspect
        if 'sessao_ativa' not in inspect(db.engine).get_table_names():
            db.create_all()  # Cria todas as tabelas que ainda não existem
            logger.info("Tabela 'sessao_ativa' criada.")

        criar_admin_padrao()
        logger.info("Sistema iniciado. Admin padrão pode importar o arquivo M3U.")
    app.run(debug=True, host='0.0.0.0', port=5000)