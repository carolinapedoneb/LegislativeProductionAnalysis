import requests
import csv

# Endpoint da API
url = f"https://dadosabertos.camara.leg.br/api/v2/referencias/eventos/codTipoEvento"

headers = {"accept": "application/json"}

tipos_de_evento = {}

# Requisição sem parâmetros de data
response = requests.get(url, headers=headers)

if response.status_code == 200:
    eventos = response.json()
    dados = eventos.get("dados", [])

    for evento in dados:
            tipos_de_evento[evento.get("cod")]= evento.get("nome")
        


    with open("tipos_de_eventos_camara.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["código", "evento"])

        for cod, evento in tipos_de_evento.items():
             writer.writerow([cod, evento])



    