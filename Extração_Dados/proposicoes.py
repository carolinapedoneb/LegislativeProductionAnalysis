import requests
import csv
from datetime import datetime, timedelta

# Função para gerar intervalos mensais
def gerar_periodos_anuais(ano):
    periodos = []
    for mes in range(1, 13):
        inicio = datetime(ano, mes, 1)
        # último dia do mês
        if mes == 12:
            fim = datetime(ano, 12, 31)
        else:
            fim = datetime(ano, mes + 1, 1) - timedelta(days=1)
        periodos.append((inicio.strftime("%Y-%m-%d"), fim.strftime("%Y-%m-%d")))
    return periodos

url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
headers = {"accept": "application/json"}

# CSV de saída
arquivo_csv = "proposicoes_2025.csv"
arquivo_ids = "proposicoes_ids_2025.txt"

all_proposicoes = []
all_ids = []

for inicio, fim in gerar_periodos_anuais(2025):
    params = {
        "dataApresentacaoInicio": inicio,
        "dataApresentacaoFim": fim,
        "ordem": "ASC",
        "ordenarPor": "id",
        "itens": 100,   # máximo permitido por página
        "pagina": 1
    }

    while True:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Erro na requisição: {response.status_code} | {inicio} a {fim}")
            break

        dados = response.json().get("dados", [])
        if not dados:
            break

        all_proposicoes.extend(dados)
        all_ids.extend([p["id"] for p in dados])

        # checa se há próxima página
        links = response.json().get("links", [])
        next_link = next((l["href"] for l in links if l["rel"] == "next"), None)
        if not next_link:
            break
        params["pagina"] += 1  # próxima página

print(f"Total de proposições coletadas: {len(all_proposicoes)}")

# Salva CSV
with open(arquivo_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["id","uri","siglaTipo","codTipo","numero","ano","ementa"])
    writer.writeheader()
    for p in all_proposicoes:
        writer.writerow({k: p.get(k, "") for k in writer.fieldnames})

# Salva IDs
with open(arquivo_ids, "w", encoding="utf-8") as f:
    for pid in all_ids:
        f.write(f"{pid}\n")
