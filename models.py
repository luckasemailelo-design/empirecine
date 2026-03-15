from database import db
from datetime import datetime

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    ativo = db.Column(db.Boolean, default=True)
    expira_em = db.Column(db.DateTime, nullable=True)
    ultimo_acesso = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    profile_pic = db.Column(db.String(200), nullable=True)  # caminho da foto de perfil
    favoritos = db.relationship('Favorito', backref='usuario', lazy=True)
    progressos = db.relationship('Progresso', backref='usuario', lazy=True)

class Canal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200))
    url = db.Column(db.String(500))
    logo = db.Column(db.String(500))
    grupo = db.Column(db.String(100))
    tvg_id = db.Column(db.String(100))
    tipo = db.Column(db.String(20))  # 'tv', 'serie', 'filme', 'radio'
    serie_nome = db.Column(db.String(200))
    temporada = db.Column(db.Integer)
    episodio = db.Column(db.Integer)
    categoria = db.Column(db.String(100))
    ano_lancamento = db.Column(db.String(4))
    tmdb_id = db.Column(db.Integer, nullable=True)          # ID da série ou filme no TMDB
    sinopse_geral = db.Column(db.Text, nullable=True)       # Sinopse do filme ou da série
    sinopse_episodio = db.Column(db.Text, nullable=True)    # Sinopse do episódio (se aplicável)

class Favorito(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    canal_id = db.Column(db.Integer, db.ForeignKey('canal.id'))
    tipo = db.Column(db.String(20))
    canal = db.relationship('Canal')

class Progresso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    canal_id = db.Column(db.Integer, db.ForeignKey('canal.id'))
    tempo = db.Column(db.Integer)
    duracao = db.Column(db.Integer)
    data_atualizacao = db.Column(db.DateTime, default=datetime.utcnow)
    canal = db.relationship('Canal')

class AdminLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    acao = db.Column(db.String(100))  # Ex: 'cadastro', 'edicao', 'banimento', 'reset_senha'
    usuario_afetado_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    descricao = db.Column(db.Text)
    data_hora = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship('Usuario', foreign_keys=[admin_id])
    usuario_afetado = db.relationship('Usuario', foreign_keys=[usuario_afetado_id])