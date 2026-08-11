# VagaNorm

Aplicação local para validar, padronizar e consolidar planilhas de vagas. O sistema executa regras automáticas primeiro, agrupa as exceções para uma revisão única e só então gera os arquivos finais.

## Fluxo

1. Upload de uma planilha `.xlsx` com a aba `Lista`.
2. Validação de cabeçalhos, colunas obrigatórias e linhas de totalização.
3. Normalização automática de UF, município/IBGE, idade, escolaridade, sexo e quantidade.
4. Agrupamento de valores não reconhecidos em pendências únicas.
5. Revisão em lote pelo operador.
6. Nova validação após as decisões.
7. Conferência obrigatória de localidades e `QTD_INDV`.
8. Exportação do Excel completo, JSON completo, `_querieData` e relatório.

As padronizações de código IBGE, faixa etária, escolaridade e sexo são sempre executadas. Elas não são configurações do operador.

Na tela inicial, o operador também escolhe como calcular `QTD_INDV` no `_querieData`:

- **regra padrão:** `max(QUANTIDADE_DE_VAGAS × 20, 250)`;
- **multiplicador personalizado:** `QUANTIDADE_DE_VAGAS × X`, sem piso mínimo, sendo `X` um inteiro positivo informado pelo operador.

O cálculo é realizado depois da soma das vagas de todas as linhas do mesmo código IBGE. A regra e o multiplicador escolhidos são registrados em `relatorio_processamento.json`.

Antes da exportação, o sistema apresenta:

- quantidade de localidades únicas, considerando códigos IBGE válidos;
- quantidade de vagas e `QTD_INDV` calculado para cada localidade;
- somatório geral de `QTD_INDV`;
- campo editável para substituir o `QTD_INDV` de cada localidade.

O somatório é atualizado imediatamente na tela. A exportação só é liberada depois que todos os valores são confirmados. O relatório final registra o total calculado, o total confirmado e cada localidade cujo valor foi alterado.

As decisões podem ser armazenadas em SQLite para reaproveitamento nas próximas planilhas. Os municípios são consultados primeiro no arquivo local `codigos_ibge_csv.csv`; por isso, a padronização normal não depende da disponibilidade da API.

## Requisitos

- Windows com Python 3.11 ou superior;
- acesso ao navegador local;
- arquivo `codigos_ibge_csv.csv` no diretório raiz da aplicação.

A API do IBGE é utilizada apenas como fallback quando uma UF não estiver disponível no CSV nem no cache SQLite.

## Instalação e execução

No PowerShell, dentro da pasta `VagaNorm2`:

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 app.py
```

A aplicação abrirá em `http://127.0.0.1:5051`.

## Entrada

A aba deve se chamar `Lista`. As colunas obrigatórias são:

- `UF`;
- `CIDADE`;
- `QUANTIDADE_DE_VAGAS`.

O sistema reconhece aliases comuns como `MUNICIPIO`, `ESTADO`, `QTD_VAGAS`, `ESCOLARIDADE` e `FAIXA_DE_IDADE`. As demais colunas esperadas são criadas vazias quando ausentes.

## Saídas

- `vagas_padronizadas.xlsx`: registros completos e colunas ordenadas;
- `vagas_padronizadas.json`: registros completos em JSON;
- `vagas_padronizadas_querieData.json`: registros consolidados por código IBGE válido;
- `relatorio_processamento.json`: opções, quantidades, alterações e alertas.

Linhas sem código IBGE válido nunca são agrupadas entre si: elas ficam no arquivo completo e são rejeitadas apenas do `_querieData`, com registro no relatório.

## Estrutura

```text
app.py
vaganorm/
  application/     pipeline e ciclo dos jobs
  domain/          regras puras e agregação
  infrastructure/  Excel, JSON, IBGE e SQLite
  web/             rotas e criação do Flask
templates/         interface HTML
static/            CSS e JavaScript
tests/             testes unitários e de integração
data/              criado em execução; banco, jobs e saídas temporárias
```

## Dados locais

O diretório `data/` é criado automaticamente e não deve ser versionado. Ele contém:

- `vaganorm.db`: cache de municípios e decisões lembradas;
- `jobs/<id>/entrada.xlsx`: upload temporário;
- `jobs/<id>/output/`: arquivos gerados.

Jobs mantidos em memória e seus diretórios são removidos quando têm mais de 24 horas e um novo processamento é iniciado.

## Catálogo local de municípios

O arquivo `codigos_ibge_csv.csv` é a fonte primária para município e código IBGE. Ele deve usar `;` como separador e conter pelo menos as colunas:

- `SIGLA UF`;
- `COD MUN`, com sete dígitos;
- `NOME MUN`.

A comparação remove acentos, espaços nas extremidades e diferença entre maiúsculas e minúsculas. Assim, `São Paulo`, `Sao Paulo` e `SAO PAULO` encontram o mesmo registro `3550308` dentro da UF `SP`.

Ordem das fontes de dados:

1. CSV local;
2. cache SQLite;
3. API do IBGE.

O estado do catálogo pode ser consultado em `GET /api/health`.

## Testes

```powershell
py -3.13 -B -m unittest discover -s tests -v
```

Os testes não consultam a internet e usam diretórios temporários.
