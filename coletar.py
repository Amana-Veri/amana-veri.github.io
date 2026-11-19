#!/usr/bin/env python3
"""
Faz login no app de revendedora, baixa o inventário e gera o catálogo (index.html).

Uso no seu computador (pergunta CPF e senha se não estiverem no ambiente):
    py coletar.py --saida docs
    py coletar.py --saida docs --baixar-fotos      (precisa de: py -m pip install pillow)

Uso no GitHub Actions: veja .github/workflows/atualizar-catalogo.yml

Variáveis de ambiente
    VQ_CPF, VQ_SENHA     login de vendedora (no GitHub: Settings > Secrets)
    VQ_INVENTARIO        id do inventário (ex.: 135136). Se vazio, tenta achar na resposta do login.
    VQ_URL_MATERIAIS     opcional: troca a URL da chamada de material de divulgação
                         (padrão: <API>/material-divulgacao). Se a chamada falhar,
                         usa a cópia de reserva materiais.json, se existir.
    VQ_NOME_LOJA, VQ_SUBTITULO, VQ_WHATSAPP   opcionais: textos do catálogo
    VQ_API               opcional: base da API (padrão abaixo)

Segurança
    - CPF, senha e token nunca são impressos.
    - Se algo falhar (login, resposta estranha, lista vazia), o catálogo anterior é mantido
      e o script termina com erro, o que faz o GitHub mandar um e-mail de aviso.
"""
import argparse
import base64
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))
from build_catalogo import montar, gerar_html, LIMITE_FOTO_DIVULGACAO  # noqa: E402

API_PADRAO = "https://gestvendas17.com.br/api/app/v3"
ORIGEM_APP = "https://vq-app.vercel.app"
UA = "Mozilla/5.0 (catalogo-revendedora; atualizacao 2x ao dia)"
FUSO_BR = timezone(timedelta(hours=-3))


class Falha(Exception):
    pass


# ---------------------------------------------------------------- HTTP
def chamar(metodo, url, token=None, corpo=None, timeout=45):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": ORIGEM_APP,
        "Referer": ORIGEM_APP + "/",
        "User-Agent": UA,
    }
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            detalhe = e.read().decode("utf-8", errors="replace")[:200]
            detalhe = re.sub(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", "***", detalhe)  # nunca imprime CPF
        except Exception:
            pass
        raise Falha(f"{metodo} {url_sem_query(url)} -> HTTP {e.code}. {detalhe}".strip())
    except urllib.error.URLError as e:
        raise Falha(f"{metodo} {url_sem_query(url)} -> sem conexão ({e.reason})")
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        raise Falha(f"{metodo} {url_sem_query(url)} -> resposta não é JSON (início: {texto[:80]!r})")


def url_sem_query(url):
    return url.split("?", 1)[0]


# ---------------------------------------------------------------- Login e token
CHAVES_TOKEN = ("access_token", "accessToken", "token", "jwt", "bearer", "id_token")


def achar_token(obj):
    """Procura o token na resposta do login, seja qual for o nome do campo."""
    if isinstance(obj, dict):
        for k in CHAVES_TOKEN:
            v = obj.get(k)
            if isinstance(v, str) and len(v) > 20:
                return v
        for v in obj.values():
            t = achar_token(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = achar_token(v)
            if t:
                return t
    elif isinstance(obj, str) and obj.startswith("eyJ") and obj.count(".") == 2:
        return obj
    return None


def validade_jwt(token):
    try:
        parte = token.split(".")[1]
        parte += "=" * (-len(parte) % 4)
        dados = json.loads(base64.urlsafe_b64decode(parte))
        if "exp" in dados:
            return datetime.fromtimestamp(int(dados["exp"]), FUSO_BR)
    except Exception:
        pass
    return None


def estrutura(obj, nivel=0):
    """Mostra só o formato da resposta (nomes de campos e tipos), sem os valores.
    Exceção: campos com 'invent' no nome mostram o valor, para achar o id do inventário."""
    pad = "  " * nivel
    if isinstance(obj, dict):
        linhas = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and nivel < 4:
                linhas.append(f"{pad}{k}:")
                linhas.append(estrutura(v, nivel + 1))
            elif "invent" in str(k).lower() and isinstance(v, (int, str)) and len(str(v)) < 20:
                linhas.append(f"{pad}{k}: {v}")
            else:
                linhas.append(f"{pad}{k}: <{type(v).__name__}>")
        return "\n".join(linhas)
    if isinstance(obj, list):
        if not obj:
            return f"{pad}[] (lista vazia)"
        return f"{pad}[lista com {len(obj)} itens; primeiro:]\n" + estrutura(obj[0], nivel + 1)
    return f"{pad}<{type(obj).__name__}>"


def achar_inventarios(obj, achados=None):
    achados = set() if achados is None else achados
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "invent" in str(k).lower() and isinstance(v, (int, str)) and str(v).isdigit():
                achados.add(str(v))
            else:
                achar_inventarios(v, achados)
    elif isinstance(obj, list):
        for v in obj:
            achar_inventarios(v, achados)
    return achados


def credenciais():
    cpf = os.environ.get("VQ_CPF", "").strip()
    senha = os.environ.get("VQ_SENHA", "")
    if (not cpf or not senha) and sys.stdin.isatty():
        cpf = cpf or input("CPF (só números): ").strip()
        senha = senha or getpass.getpass("Senha (não aparece ao digitar): ")
    if not cpf or not senha:
        raise Falha("Faltam VQ_CPF e/ou VQ_SENHA.")
    return "".join(c for c in cpf if c.isdigit()), senha


# ---------------------------------------------------------------- Principal
def main():
    ap = argparse.ArgumentParser(description="Atualiza o catálogo a partir do app de revendedora.")
    ap.add_argument("--saida", default="docs", help="pasta onde gravar index.html (padrão: docs)")
    ap.add_argument("--baixar-fotos", action="store_true", help="baixa e comprime as fotos (precisa de Pillow)")
    ap.add_argument("--diagnostico", action="store_true", help="mostra o formato da resposta do login, sem valores")
    ap.add_argument("--min-pecas", type=int, default=1, help="aborta se vierem menos peças que isso (padrão: 1)")
    ap.add_argument("--sem-fotos-site", action="store_true", help="não procura fotos no site oficial da Veri")
    args = ap.parse_args()

    api = os.environ.get("VQ_API", API_PADRAO).rstrip("/")
    saida = Path(args.saida)
    if not saida.is_absolute():
        saida = PASTA / saida

    # 1. Login
    cpf, senha = credenciais()
    login = chamar("POST", f"{api}/auth/login", corpo={"cpf": cpf, "password": senha})
    del senha
    token = achar_token(login)
    if args.diagnostico or not token:
        print("Formato da resposta do login (sem valores):")
        print(estrutura(login))
    if not token:
        raise Falha("Login respondeu, mas não achei o token. Rode com --diagnostico e me mande o formato acima.")
    validade = validade_jwt(token)
    print("Login OK." + (f" Token válido até {validade:%d/%m/%Y %H:%M}." if validade else ""))

    # 2. Inventário
    inventario = os.environ.get("VQ_INVENTARIO", "").strip()
    if not inventario:
        candidatos = achar_inventarios(login)
        if len(candidatos) == 1:
            inventario = candidatos.pop()
            print(f"Inventário encontrado no login: {inventario}")
        else:
            raise Falha(f"Defina VQ_INVENTARIO (achei no login: {sorted(candidatos) or 'nenhum'}).")

    resp_pecas = chamar("GET", f"{api}/inventarios/{inventario}/pecas", token=token)

    # 3. Material de divulgação (fotos com modelo). Se falhar, usa a cópia de reserva.
    materiais = None
    url_mat = os.environ.get("VQ_URL_MATERIAIS", "").strip() or f"{api}/material-divulgacao"
    try:
        materiais = chamar("GET", url_mat, token=token)
        print(f"Material de divulgação: {len(materiais.get('data') or []) if isinstance(materiais, dict) else '?'} fotos.")
    except Falha as e:
        reserva = PASTA / "materiais.json"
        print(f"Aviso: material de divulgação indisponível ({e}).",
              "Usando a cópia de reserva." if reserva.exists() else "Sigo sem fotos com modelo.")
        if reserva.exists():
            materiais = json.loads(reserva.read_text(encoding="utf-8"))

    # 4. Monta itens (só campos públicos) e confere
    limite = None if args.baixar_fotos else LIMITE_FOTO_DIVULGACAO
    itens, resumo = montar(resp_pecas, materiais, limite_bytes=limite)
    if resumo["pecas_no_catalogo"] < args.min_pecas:
        raise Falha(f"Só {resumo['pecas_no_catalogo']} peça(s) disponível(is); mantive o catálogo anterior.")

    # 5. Fotos oficiais do site da Veri, quando o SKU da peça existe lá (senão ficam as do app)
    if not args.sem_fotos_site:
        from fotos_site import aplicar
        resumo.update(aplicar(itens, PASTA / "cache_site.json"))

    # 6. Fotos locais comprimidas (opcional)
    if args.baixar_fotos:
        from fotos import processar_fotos
        resumo.update(processar_fotos(itens, saida / "fotos", prefixo="fotos/"))

    # 7. Grava o catálogo (escreve num temporário e troca, para nunca deixar arquivo pela metade)
    agora = datetime.now(FUSO_BR)
    config = {"atualizadoEm": agora.strftime("%d/%m/%Y às %H:%M")}
    for chave, env in (("nomeLoja", "VQ_NOME_LOJA"), ("subtitulo", "VQ_SUBTITULO"), ("whatsapp", "VQ_WHATSAPP")):
        if os.environ.get(env, "").strip():
            config[chave] = os.environ[env].strip()
    saida.mkdir(parents=True, exist_ok=True)
    tmp = saida / "index.html.tmp"
    tmp.write_text(gerar_html(itens, config), encoding="utf-8")
    tmp.replace(saida / "index.html")
    (saida / ".nojekyll").touch()

    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    print(f"Catálogo gravado em {saida / 'index.html'}")


if __name__ == "__main__":
    try:
        main()
    except Falha as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(2)
