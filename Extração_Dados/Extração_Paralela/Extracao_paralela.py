import requests
import csv
import time
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
from threading import Lock
from pathlib import Path
import json

class CamaraAPIExtractor:
    """Extrator paralelo e inteligente para API da Câmara dos Deputados"""
    
    def __init__(self, max_workers=5, requests_per_second=10):
        self.max_workers = max_workers
        self.delay_between_requests = 1.0 / requests_per_second
        self.session = self._create_session()
        self.lock = Lock()
        self.last_request_time = time.time()
        
    def _create_session(self):
        """Cria sessão com retry automático"""
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
    
    def _rate_limited_request(self, url, params=None):
        """Faz requisição respeitando rate limit"""
        with self.lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.delay_between_requests:
                time.sleep(self.delay_between_requests - elapsed)
            self.last_request_time = time.time()
        
        headers = {"accept": "application/json"}
        response = self.session.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def _gerar_periodos_mensais(self, data_inicio, data_fim):
        """Gera períodos mensais para quebrar requisições grandes"""
        periodos = []
        current = datetime.strptime(data_inicio, "%Y-%m-%d")
        end = datetime.strptime(data_fim, "%Y-%m-%d")
        
        while current <= end:
            inicio_mes = current.replace(day=1)
            if current.month == 12:
                fim_mes = current.replace(day=31)
            else:
                proximo_mes = current.replace(month=current.month + 1, day=1)
                fim_mes = proximo_mes - timedelta(days=1)
            
            if fim_mes > end:
                fim_mes = end
            
            periodos.append((
                inicio_mes.strftime("%Y-%m-%d"),
                fim_mes.strftime("%Y-%m-%d")
            ))
            
            current = fim_mes + timedelta(days=1)
        
        return periodos
    
    def _clean_text(self, value):
        """Limpa texto removendo quebras de linha"""
        if value is None:
            return ""
        s = str(value)
        s = s.replace("\r", " ").replace("\n", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s
    
    # ========== EVENTOS ==========
    
    def extrair_eventos(self, data_inicio="2023-02-01", data_fim=None):
        """Extrai todos os eventos no período"""
        if data_fim is None:
            data_fim = datetime.now().strftime("%Y-%m-%d")
        
        print(f"\n=== EXTRAINDO EVENTOS ({data_inicio} a {data_fim}) ===")
        
        periodos = self._gerar_periodos_mensais(data_inicio, data_fim)
        todos_eventos = []
        
        def processar_periodo(periodo):
            inicio, fim = periodo
            eventos_periodo = []
            pagina = 1
            
            while True:
                try:
                    params = {
                        "ordem": "ASC",
                        "ordenarPor": "dataHoraInicio",
                        "itens": 100,
                        "dataInicio": inicio,
                        "dataFim": fim,
                        "pagina": pagina
                    }
                    
                    dados = self._rate_limited_request(
                        "https://dadosabertos.camara.leg.br/api/v2/eventos",
                        params
                    )
                    
                    eventos = dados.get("dados", [])
                    if not eventos:
                        break
                    
                    for evento in eventos:
                        evento_dict = {
                            "id": evento.get("id"),
                            "uri": evento.get("uri"),
                            "dataHoraInicio": evento.get("dataHoraInicio"),
                            "dataHoraFim": evento.get("dataHoraFim"),
                            "situacao": evento.get("situacao"),
                            "descricaoTipo": evento.get("descricaoTipo"),
                            "descricao": self._clean_text(evento.get("descricao")),
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
                        
                        eventos_periodo.append(evento_dict)
                    
                    print(f"  Período {inicio}: página {pagina} ({len(eventos_periodo)} eventos)")
                    pagina += 1
                    
                except Exception as e:
                    print(f"  Erro no período {inicio}, página {pagina}: {e}")
                    break
            
            return eventos_periodo
        
        # Processa períodos em paralelo
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_periodo, p): p for p in periodos}
            
            for future in as_completed(futures):
                try:
                    eventos = future.result()
                    todos_eventos.extend(eventos)
                except Exception as e:
                    print(f"Erro ao processar período: {e}")
        
        # Salva eventos
        arquivo = "Eventos_Camara.csv"
        if todos_eventos:
            df = pd.DataFrame(todos_eventos)
            df.to_csv(arquivo, index=False, encoding="utf-8")
            print(f"\n✓ {len(todos_eventos)} eventos salvos em {arquivo}")
        
        return todos_eventos
    
    # ========== DEPUTADOS POR EVENTO ==========
    
    def extrair_deputados_eventos(self, arquivo_eventos="Eventos_Camara.csv"):
        """Extrai deputados que participaram de cada evento"""
        print(f"\n=== EXTRAINDO DEPUTADOS DOS EVENTOS ===")
        
        eventos_df = pd.read_csv(arquivo_eventos)
        evento_ids = eventos_df["id"].tolist()
        
        arquivo_saida = "Deputados_Eventos.csv"
        fieldnames = [
            "id_evento", "id_deputado", "uri", "nome", "siglaPartido",
            "uriPartido", "siglaUf", "idLegislatura", "urlFoto", "email"
        ]
        
        # Cria arquivo vazio
        with open(arquivo_saida, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        
        write_lock = Lock()
        total_deputados = 0
        eventos_sem_deputados = []
        
        def processar_evento(evento_id):
            nonlocal total_deputados
            url = f"https://dadosabertos.camara.leg.br/api/v2/eventos/{evento_id}/deputados"
            
            try:
                dados = self._rate_limited_request(url)
                deputados = dados.get("dados", [])
                
                if not deputados:
                    eventos_sem_deputados.append(evento_id)
                    return 0
                
                rows = []
                for dep in deputados:
                    rows.append({
                        "id_evento": self._clean_text(evento_id),
                        "id_deputado": self._clean_text(dep.get("id")),
                        "uri": self._clean_text(dep.get("uri")),
                        "nome": self._clean_text(dep.get("nome")),
                        "siglaPartido": self._clean_text(dep.get("siglaPartido")),
                        "uriPartido": self._clean_text(dep.get("uriPartido")),
                        "siglaUf": self._clean_text(dep.get("siglaUf")),
                        "idLegislatura": self._clean_text(dep.get("idLegislatura")),
                        "urlFoto": self._clean_text(dep.get("urlFoto")),
                        "email": self._clean_text(dep.get("email"))
                    })
                
                # Escreve em lote
                with write_lock:
                    with open(arquivo_saida, "a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writerows(rows)
                    total_deputados += len(rows)
                
                return len(rows)
                
            except Exception as e:
                print(f"  Erro no evento {evento_id}: {e}")
                return 0
        
        # Processa eventos em paralelo
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_evento, eid): eid for eid in evento_ids}
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    future.result()
                    if i % 100 == 0:
                        print(f"  Processados {i}/{len(evento_ids)} eventos ({total_deputados} deputados)")
                except Exception as e:
                    print(f"  Erro: {e}")
        
        print(f"\n✓ {total_deputados} deputados salvos em {arquivo_saida}")
        print(f"  {len(eventos_sem_deputados)} eventos sem deputados")
        
        return total_deputados
    
    # ========== PROPOSIÇÕES ==========
    
    def extrair_proposicoes(self, data_inicio="2023-02-01", data_fim=None):
        """Extrai todas as proposições no período"""
        if data_fim is None:
            data_fim = datetime.now().strftime("%Y-%m-%d")
        
        print(f"\n=== EXTRAINDO PROPOSIÇÕES ({data_inicio} a {data_fim}) ===")
        
        periodos = self._gerar_periodos_mensais(data_inicio, data_fim)
        todas_proposicoes = []
        
        def processar_periodo(periodo):
            inicio, fim = periodo
            proposicoes_periodo = []
            pagina = 1
            
            while True:
                try:
                    params = {
                        "dataApresentacaoInicio": inicio,
                        "dataApresentacaoFim": fim,
                        "ordem": "ASC",
                        "ordenarPor": "id",
                        "itens": 100,
                        "pagina": pagina
                    }
                    
                    dados = self._rate_limited_request(
                        "https://dadosabertos.camara.leg.br/api/v2/proposicoes",
                        params
                    )
                    
                    proposicoes = dados.get("dados", [])
                    if not proposicoes:
                        break
                    
                    proposicoes_periodo.extend(proposicoes)
                    print(f"  Período {inicio}: página {pagina} ({len(proposicoes_periodo)} proposições)")
                    pagina += 1
                    
                except Exception as e:
                    print(f"  Erro no período {inicio}, página {pagina}: {e}")
                    break
            
            return proposicoes_periodo
        
        # Processa períodos em paralelo
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_periodo, p): p for p in periodos}
            
            for future in as_completed(futures):
                try:
                    proposicoes = future.result()
                    todas_proposicoes.extend(proposicoes)
                except Exception as e:
                    print(f"Erro ao processar período: {e}")
        
        # Salva proposições
        arquivo = "Proposicoes_Camara.csv"
        if todas_proposicoes:
            df = pd.DataFrame(todas_proposicoes)
            df.to_csv(arquivo, index=False, encoding="utf-8")
            print(f"\n✓ {len(todas_proposicoes)} proposições salvas em {arquivo}")
        
        return todas_proposicoes
    
    # ========== VOTAÇÕES POR PROPOSIÇÃO ==========
    
    def extrair_votacoes_proposicoes(self, arquivo_proposicoes="Proposicoes_Camara.csv"):
        """Extrai votações de cada proposição"""
        print(f"\n=== EXTRAINDO VOTAÇÕES DAS PROPOSIÇÕES ===")
        
        proposicoes_df = pd.read_csv(arquivo_proposicoes)
        proposicao_ids = proposicoes_df["id"].tolist()
        
        arquivo_saida = "Votacoes_por_Proposicao.csv"
        fieldnames = [
            "id_votacao", "uri", "data", "dataHoraRegistro", "siglaOrgao",
            "uriOrgao", "uriEvento", "proposicaoObjeto", "uriProposicaoObjeto",
            "descricao", "aprovacao", "id_proposicao"
        ]
        
        with open(arquivo_saida, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        
        write_lock = Lock()
        total_votacoes = 0
        
        def processar_proposicao(prop_id):
            nonlocal total_votacoes
            url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{prop_id}/votacoes"
            params = {"ordem": "DESC", "ordenarPor": "dataHoraRegistro"}
            
            try:
                dados = self._rate_limited_request(url, params)
                votacoes = dados.get("dados", [])
                
                if not votacoes:
                    return 0
                
                rows = []
                for v in votacoes:
                    rows.append({
                        "id_votacao": v.get("id"),
                        "uri": v.get("uri"),
                        "data": v.get("data"),
                        "dataHoraRegistro": v.get("dataHoraRegistro"),
                        "siglaOrgao": v.get("siglaOrgao"),
                        "uriOrgao": v.get("uriOrgao"),
                        "uriEvento": v.get("uriEvento"),
                        "proposicaoObjeto": v.get("proposicaoObjeto"),
                        "uriProposicaoObjeto": v.get("uriProposicaoObjeto"),
                        "descricao": self._clean_text(v.get("descricao")),
                        "aprovacao": v.get("aprovacao"),
                        "id_proposicao": prop_id
                    })
                
                with write_lock:
                    with open(arquivo_saida, "a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writerows(rows)
                    total_votacoes += len(rows)
                
                return len(rows)
                
            except Exception as e:
                print(f"  Erro na proposição {prop_id}: {e}")
                return 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_proposicao, pid): pid for pid in proposicao_ids}
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    future.result()
                    if i % 100 == 0:
                        print(f"  Processadas {i}/{len(proposicao_ids)} proposições ({total_votacoes} votações)")
                except Exception as e:
                    print(f"  Erro: {e}")
        
        print(f"\n✓ {total_votacoes} votações salvas em {arquivo_saida}")
        return total_votacoes
    
    # ========== VOTOS POR VOTAÇÃO ==========
    
    def extrair_votos_votacoes(self, arquivo_votacoes="Votacoes_por_Proposicao.csv"):
        """Extrai votos individuais de cada votação"""
        print(f"\n=== EXTRAINDO VOTOS DAS VOTAÇÕES ===")
        
        votacoes_df = pd.read_csv(arquivo_votacoes)
        votacao_ids = votacoes_df["id_votacao"].dropna().unique().tolist()
        
        arquivo_saida = "Votos_Proposicoes.csv"
        fieldnames = [
            "id_votacao", "dataRegistroVoto", "tipoVoto", "deputado_id",
            "deputado_nome", "deputado_siglaPartido", "deputado_siglaUf",
            "deputado_idLegislatura", "deputado_email", "deputado_uri",
            "deputado_uriPartido", "deputado_urlFoto"
        ]
        
        with open(arquivo_saida, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        
        write_lock = Lock()
        total_votos = 0
        
        def processar_votacao(votacao_id):
            nonlocal total_votos
            url = f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{votacao_id}/votos"
            
            try:
                dados = self._rate_limited_request(url)
                votos = dados.get("dados", [])
                
                if not votos:
                    return 0
                
                rows = []
                for v in votos:
                    dep = v.get("deputado_", {})
                    rows.append({
                        "id_votacao": votacao_id,
                        "dataRegistroVoto": v.get("dataRegistroVoto"),
                        "tipoVoto": v.get("tipoVoto"),
                        "deputado_id": dep.get("id"),
                        "deputado_nome": self._clean_text(dep.get("nome")),
                        "deputado_siglaPartido": dep.get("siglaPartido"),
                        "deputado_siglaUf": dep.get("siglaUf"),
                        "deputado_idLegislatura": dep.get("idLegislatura"),
                        "deputado_email": dep.get("email"),
                        "deputado_uri": dep.get("uri"),
                        "deputado_uriPartido": dep.get("uriPartido"),
                        "deputado_urlFoto": dep.get("urlFoto")
                    })
                
                with write_lock:
                    with open(arquivo_saida, "a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writerows(rows)
                    total_votos += len(rows)
                
                return len(rows)
                
            except Exception as e:
                print(f"  Erro na votação {votacao_id}: {e}")
                return 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_votacao, vid): vid for vid in votacao_ids}
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    future.result()
                    if i % 100 == 0:
                        print(f"  Processadas {i}/{len(votacao_ids)} votações ({total_votos} votos)")
                except Exception as e:
                    print(f"  Erro: {e}")
        
        print(f"\n✓ {total_votos} votos salvos em {arquivo_saida}")
        return total_votos


# ========== EXECUÇÃO PRINCIPAL ==========

if __name__ == "__main__":
    # Configuração: ajuste conforme necessário
    # max_workers: número de threads paralelas (5-10 é seguro)
    # requests_per_second: taxa de requisições (10 é conservador)
    extractor = CamaraAPIExtractor(max_workers=8, requests_per_second=10)
    
    data_inicio = "2023-02-01"
    data_fim = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n{'='*60}")
    print(f"EXTRAÇÃO DE DADOS DA CÂMARA DOS DEPUTADOS")
    print(f"Período: {data_inicio} até {data_fim}")
    print(f"{'='*60}")
    
    inicio_geral = time.time()
    
    # 1. Extrair eventos
    extractor.extrair_eventos(data_inicio, data_fim)
    
    # 2. Extrair deputados dos eventos
    extractor.extrair_deputados_eventos()
    
    # 3. Extrair proposições
    extractor.extrair_proposicoes(data_inicio, data_fim)
    
    # 4. Extrair votações das proposições
    extractor.extrair_votacoes_proposicoes()
    
    # 5. Extrair votos das votações
    extractor.extrair_votos_votacoes()
    
    tempo_total = time.time() - inicio_geral
    print(f"\n{'='*60}")
    print(f"✓ EXTRAÇÃO CONCLUÍDA EM {tempo_total/60:.1f} MINUTOS")
    print(f"{'='*60}")
    
    # Exibe resumo dos arquivos gerados
    arquivos = [
        "Eventos_Camara.csv",
        "Deputados_Eventos.csv",
        "Proposicoes_Camara.csv",
        "Votacoes_por_Proposicao.csv",
        "Votos_Proposicoes.csv"
    ]
    
    print("\nARQUIVOS GERADOS:")
    for arquivo in arquivos:
        if Path(arquivo).exists():
            linhas = sum(1 for _ in open(arquivo, encoding="utf-8")) - 1
            print(f"  • {arquivo}: {linhas:,} registros")