#!/usr/bin/env python3
"""
Transforma as respostas da API de revendedora em itens de catálogo e gera o HTML.

Pode ser usado sozinho, com JSONs salvos à mão:
    python build_catalogo.py pecas.json materiais.json saida.html

Só vão para o catálogo campos que podem ser públicos.
Nunca saem daqui: nome/ID de cliente, venda, carrinho, nº de série, % de desconto.
"""
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

LIMITE_FOTO_DIVULGACAO = 2_000_000  # bytes; usado quando as fotos NÃO são baixadas e comprimidas

NORMALIZA_BANHO = {"Rodio": "Ródio", "Ouro/Ródio": "Ouro e Ródio"}
NORMALIZA_SUB = {"Delicadas": "Delicada", "Delicados": "Delicado"}

PASTA = Path(__file__).resolve().parent
TEMPLATE = PASTA / "template.html"


def extrair_pecas(resposta):
    """Aceita {"data": {"pecas": [...]}} (formato atual) e variações simples."""
    if isinstance(resposta, dict):
        data = resposta.get("data", resposta)
        if isinstance(data, dict) and isinstance(data.get("pecas"), list):
            return data["pecas"]
        if isinstance(data, list):
            return data
    if isinstance(resposta, list):
        return resposta
    raise ValueError("Formato inesperado na resposta de peças (não achei a lista 'pecas').")


def extrair_materiais(resposta):
    if not resposta:
        return []
    if isinstance(resposta, dict):
        resposta = resposta.get("data", [])
    return resposta if isinstance(resposta, list) else []


def montar(pecas_raw, materiais_raw=None, limite_bytes=LIMITE_FOTO_DIVULGACAO):
    pecas = extrair_pecas(pecas_raw)
    materiais = extrair_materiais(materiais_raw)

    # Fotos de divulgação (com modelo) por código de modelo, das mais leves para as mais pesadas
    fotos_modelo = defaultdict(list)
    ignoradas = 0
    for m in sorted(materiais, key=lambda x: x.get("tamanho") or 0):
        if m.get("tipo") != "imagem" or not m.get("url"):
            continue
        if limite_bytes and (m.get("tamanho") or 0) > limite_bytes:
            ignoradas += 1
            continue
        for cod in m.get("codigos_modelos") or []:
            if m["url"] not in fotos_modelo[cod.upper()]:
                fotos_modelo[cod.upper()].append(m["url"])

    # Agrupa peças disponíveis por SKU completo (mesmo modelo + banho + pedra)
    grupos = OrderedDict()
    vendidas = 0
    for p in pecas:
        if p.get("venda_id") or p.get("carrinho_item_id") or p.get("data_venda"):
            vendidas += 1
            continue
        sku = p.get("full_sku") or f'{p.get("modelo")}-{p.get("banho")}-{p.get("descricao")}'
        if sku not in grupos:
            imgs = p.get("images") or ([p["imagem"]] if p.get("imagem") else [])
            imgs = sorted(set(imgs), key=lambda u: u.rsplit("-", 1)[-1])
            modelo = str(p.get("modelo") or "").upper()
            descricao = " / ".join(s.strip() for s in str(p.get("descricao") or "").split("/") if s.strip())
            grupos[sku] = {
                "sku": sku,
                "modelo": modelo,
                "descricao": descricao,
                "valor": p.get("valor"),
                "categoria": p.get("categoria") or "Outros",
                "subcategoria": NORMALIZA_SUB.get(p.get("subcategoria"), p.get("subcategoria")),
                "banho": NORMALIZA_BANHO.get(p.get("banho"), p.get("banho")),
                "aro": p.get("aro"),
                "colecao": p.get("colecao"),
                "infantil": p.get("linha") == "infantil",
                "capa": imgs[0] if imgs else None,
                "fotos": imgs,
                "fotos_modelo": list(fotos_modelo.get(modelo, [])),
                "qtd": 0,
            }
        grupos[sku]["qtd"] += 1

    itens = [i for i in grupos.values() if isinstance(i["valor"], (int, float))]
    resumo = {
        "pecas_na_api": len(pecas),
        "vendidas_excluidas": vendidas,
        "pecas_no_catalogo": sum(i["qtd"] for i in itens),
        "itens_no_catalogo": len(itens),
        "fotos_divulgacao_puladas_por_peso": ignoradas,
    }
    return itens, resumo


def gerar_html(itens, config, template_path=TEMPLATE):
    tpl = Path(template_path).read_text(encoding="utf-8")
    seguro = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if "/*__DADOS__*/[]" not in tpl or "/*__CONFIG__*/{}" not in tpl:
        raise ValueError("template.html sem os marcadores /*__DADOS__*/[] e /*__CONFIG__*/{}")
    return tpl.replace("/*__DADOS__*/[]", seguro(itens)).replace("/*__CONFIG__*/{}", seguro(config))


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    pecas_path, mat_path, out_path = sys.argv[1:]
    carregar = lambda c: json.loads(Path(c).read_text(encoding="utf-8"))
    itens, resumo = montar(carregar(pecas_path), carregar(mat_path))
    Path(out_path).write_text(gerar_html(itens, {}), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
