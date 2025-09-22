import requests
import csv

url_base = "https://dadosabertos.camara.leg.br/api/v2/eventos"

params = {
    "ordem": "ASC",
    "ordenarPor": "dataHoraInicio",
    "itens": 100,  # máximo permitido
    "dataInicio": "2025-01-01",
    "dataFim": "2025-12-31"
}

headers = {"accept": "application/json"}

lista_de_eventos = []
pagina = 1

while True:
    params["pagina"] = pagina
    response = requests.get(url_base, headers=headers, params=params)

    if response.status_code != 200:
        print(f"Erro: {response.status_code}")
        break

    eventos = response.json().get("dados", [])
    if not eventos:  # se não tiver mais eventos, para o loop
        break

    for evento in eventos:
        evento_dict = {
            "id": evento.get("id"),
            "uri": evento.get("uri"),
            "dataHoraInicio": evento.get("dataHoraInicio"),
            "dataHoraFim": evento.get("dataHoraFim"),
            "situacao": evento.get("situacao"),
            "descricaoTipo": evento.get("descricaoTipo"),
            "descricao": evento.get("descricao", "").replace("\n", " ").replace("\r", " "),
            "localExterno": evento.get("localExterno")
        }

        orgao = evento.get("orgaos", [{}])[0] if evento.get("orgaos") else {}
        evento_dict.update({
            "orgao_id": orgao.get("id"),
            "orgao_sigla": orgao.get("sigla"),
            "orgao_nome": orgao.get("nome"),
        })

        local = evento.get("localCamara", {}) or {}
        evento_dict.update({
            "local_nome": local.get("nome"),
            "local_predio": local.get("predio"),
            "local_sala": local.get("sala"),
            "local_andar": local.get("andar"),
        })

        lista_de_eventos.append(evento_dict)

    print(f"Página {pagina} processada ({len(lista_de_eventos)} eventos coletados).")
    pagina += 1

# salva em CSV
with open("Eventos_Camara.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=lista_de_eventos[0].keys())
    writer.writeheader()
    writer.writerows(lista_de_eventos)

print(f"Coleta finalizada: {len(lista_de_eventos)} eventos salvos.")
