import requests
import csv
import time
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
from threading import Lock
from pathlib import Path

class ExtractorVotacoesTurbo:
    """Extrator turbinado para votações - continua do índice 4334"""
    
    def __init__(self, max_workers=15, requests_per_second=20):
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
        adapter = HTTPAdapter(
            max_retries=retry_strategy, 
            pool_connections=20, 
            pool_maxsize=20
        )
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
    
    def _clean_text(self, value):
        """Limpa texto removendo quebras de linha"""
        if value is None:
            return ""
        s = str(value)
        s = s.replace("\r", " ").replace("\n", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s
    
    def extrair_votacoes_do_indice(self, 
                                   arquivo_proposicoes="Proposicoes_Camara.csv",
                                   indice_inicial=4334,
                                   arquivo_saida="Votacoes_por_Proposicao.csv",
                                   arquivo_checkpoint="checkpoint_votacoes.txt"):
        """
        Extrai votações continuando do índice especificado
        
        Args:
            arquivo_proposicoes: CSV com todas as proposições
            indice_inicial: Índice para começar (4334 = já foram processadas 0-4333)
            arquivo_saida: Onde salvar as votações
            arquivo_checkpoint: Arquivo de checkpoint para continuar se parar
        """
        
        print(f"\n{'='*70}")
        print(f"🚀 EXTRATOR TURBO DE VOTAÇÕES - MODO CONTINUAÇÃO")
        print(f"{'='*70}")
        
        # Carrega proposições
        print(f"\n📂 Carregando proposições de {arquivo_proposicoes}...")
        proposicoes_df = pd.read_csv(arquivo_proposicoes)
        total_proposicoes = len(proposicoes_df)
        
        print(f"   Total de proposições no arquivo: {total_proposicoes:,}")
        print(f"   Já processadas (índices 0-{indice_inicial-1}): {indice_inicial:,}")
        print(f"   🎯 Restantes (índice {indice_inicial}+): {total_proposicoes - indice_inicial:,}")
        
        # Pega apenas as proposições a partir do índice especificado
        proposicoes_restantes = proposicoes_df.iloc[indice_inicial:].copy()
        proposicao_ids = proposicoes_restantes["id"].tolist()
        
        # Verifica checkpoint para pular as que já foram processadas NESTA execução
        processados_agora = set()
        if Path(arquivo_checkpoint).exists():
            with open(arquivo_checkpoint, "r") as f:
                processados_agora = set(int(line.strip()) for line in f if line.strip())
            if processados_agora:
                print(f"\n   📌 Checkpoint encontrado: {len(processados_agora)} proposições já processadas nesta sessão")
                proposicao_ids = [pid for pid in proposicao_ids if pid not in processados_agora]
                print(f"   🔄 Continuando de onde parou: {len(proposicao_ids):,} proposições restantes")
        
        if not proposicao_ids:
            print(f"\n✅ Todas as proposições já foram processadas!")
            return 0
        
        # Configuração dos campos
        fieldnames = [
            "id_votacao", "uri", "data", "dataHoraRegistro", "siglaOrgao",
            "uriOrgao", "uriEvento", "proposicaoObjeto", "uriProposicaoObjeto",
            "descricao", "aprovacao", "id_proposicao"
        ]
        
        # Cria/valida arquivo de saída
        arquivo_existe = Path(arquivo_saida).exists()
        if not arquivo_existe:
            print(f"\n⚠️  Arquivo {arquivo_saida} não encontrado, criando novo...")
            with open(arquivo_saida, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
        else:
            print(f"\n✓ Usando arquivo existente: {arquivo_saida}")
        
        # Locks para thread-safety
        write_lock = Lock()
        checkpoint_lock = Lock()
        total_votacoes = 0
        proposicoes_com_votacao = 0
        proposicoes_sem_votacao = 0
        
        def processar_proposicao(prop_id):
            nonlocal total_votacoes, proposicoes_com_votacao, proposicoes_sem_votacao
            
            url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{prop_id}/votacoes"
            params = {"ordem": "DESC", "ordenarPor": "dataHoraRegistro"}
            
            try:
                dados = self._rate_limited_request(url, params)
                votacoes = dados.get("dados", [])
                
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
                
                if rows:
                    with write_lock:
                        with open(arquivo_saida, "a", newline="", encoding="utf-8") as f:
                            writer = csv.DictWriter(f, fieldnames=fieldnames)
                            writer.writerows(rows)
                        total_votacoes += len(rows)
                        proposicoes_com_votacao += 1
                else:
                    proposicoes_sem_votacao += 1
                
                # Salva checkpoint
                with checkpoint_lock:
                    with open(arquivo_checkpoint, "a") as f:
                        f.write(f"{prop_id}\n")
                
                return len(rows)
                
            except Exception as e:
                print(f"\n   ⚠️  Erro na proposição {prop_id}: {e}")
                # Salva checkpoint mesmo em erro para não reprocessar
                with checkpoint_lock:
                    with open(arquivo_checkpoint, "a") as f:
                        f.write(f"{prop_id}\n")
                return 0
        
        # Informações da execução
        print(f"\n{'='*70}")
        print(f"⚙️  CONFIGURAÇÃO:")
        print(f"   • Threads paralelas: {self.max_workers}")
        print(f"   • Taxa de requisições: {1.0/self.delay_between_requests:.0f} req/s")
        print(f"   • Proposições a processar: {len(proposicao_ids):,}")
        print(f"{'='*70}\n")
        
        print(f"🔥 INICIANDO EXTRAÇÃO...\n")
        
        inicio = time.time()
        
        # Processa em paralelo
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(processar_proposicao, pid): pid for pid in proposicao_ids}
            
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    future.result()
                    
                    # Atualiza progresso a cada 500 proposições
                    if i % 500 == 0 or i == len(proposicao_ids):
                        elapsed = time.time() - inicio
                        rate = i / elapsed if elapsed > 0 else 0
                        restante = (len(proposicao_ids) - i) / rate if rate > 0 else 0
                        
                        print(f"📊 Progresso: {i:,}/{len(proposicao_ids):,} ({i/len(proposicao_ids)*100:.1f}%)")
                        print(f"   • Votações encontradas: {total_votacoes:,}")
                        print(f"   • Com votação: {proposicoes_com_votacao:,} | Sem votação: {proposicoes_sem_votacao:,}")
                        print(f"   • Velocidade: {rate:.1f} prop/s")
                        print(f"   • Tempo decorrido: {elapsed/60:.1f} min")
                        print(f"   • ⏱️  ETA: {restante/60:.0f} min ({restante/3600:.1f}h)")
                        print(f"   {'-'*68}\n")
                        
                except Exception as e:
                    print(f"   ❌ Erro inesperado: {e}\n")
        
        tempo_total = time.time() - inicio
        
        # Remove checkpoint ao finalizar com sucesso
        if Path(arquivo_checkpoint).exists():
            Path(arquivo_checkpoint).unlink()
            print(f"✓ Checkpoint removido (extração completa)")
        
        # Resumo final
        print(f"\n{'='*70}")
        print(f"✅ EXTRAÇÃO FINALIZADA!")
        print(f"{'='*70}")
        print(f"📈 ESTATÍSTICAS:")
        print(f"   • Proposições processadas: {len(proposicao_ids):,}")
        print(f"   • Votações extraídas: {total_votacoes:,}")
        print(f"   • Proposições com votação: {proposicoes_com_votacao:,}")
        print(f"   • Proposições sem votação: {proposicoes_sem_votacao:,}")
        print(f"   • Tempo total: {tempo_total/60:.1f} minutos ({tempo_total/3600:.2f} horas)")
        print(f"   • Velocidade média: {len(proposicao_ids)/tempo_total:.1f} prop/s")
        print(f"\n💾 Arquivo salvo: {arquivo_saida}")
        
        # Mostra tamanho do arquivo
        if Path(arquivo_saida).exists():
            linhas = sum(1 for _ in open(arquivo_saida, encoding="utf-8")) - 1
            print(f"   Total de linhas no arquivo: {linhas:,}")
        
        print(f"{'='*70}\n")
        
        return total_votacoes


# ========== EXECUÇÃO ==========

if __name__ == "__main__":
    
    print(f"\n{'#'*70}")
    print(f"#  EXTRAÇÃO DE VOTAÇÕES POR PROPOSIÇÃO - MODO CONTINUAÇÃO")
    print(f"#  Início: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*70}\n")
    
    # Cria o extrator com configuração ULTRA turbinada (meta: 1 hora)
    extractor = ExtractorVotacoesTurbo(
        max_workers=50,          # 50 threads paralelas (MÁXIMO)
        requests_per_second=60   # 60 requisições por segundo (AGRESSIVO)
    )
    
    # CONFIGURAÇÃO: ajuste estes valores conforme necessário
    ARQUIVO_PROPOSICOES = "Proposicoes_Camara.csv"
    INDICE_INICIAL = 4334  # Começa do índice 4334
    ARQUIVO_SAIDA = "Votacoes_por_Proposicao.csv"
    
    try:
        # Executa a extração
        total = extractor.extrair_votacoes_do_indice(
            arquivo_proposicoes=ARQUIVO_PROPOSICOES,
            indice_inicial=INDICE_INICIAL,
            arquivo_saida=ARQUIVO_SAIDA
        )
        
        print(f"\n🎉 SUCESSO! {total:,} votações extraídas!")
        
    except KeyboardInterrupt:
        print(f"\n\n⚠️  INTERROMPIDO PELO USUÁRIO")
        print(f"💾 Progresso salvo em checkpoint_votacoes.txt")
        print(f"🔄 Execute novamente para continuar de onde parou!\n")
        
    except Exception as e:
        print(f"\n\n❌ ERRO FATAL: {e}")
        print(f"💾 Progresso salvo em checkpoint_votacoes.txt")
        print(f"🔄 Execute novamente para continuar de onde parou!\n")
        import traceback
        traceback.print_exc()
    
    finally:
        print(f"\n{'#'*70}")
        print(f"#  Fim: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*70}\n")