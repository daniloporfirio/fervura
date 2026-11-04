# Fervura: instruções para o Claude

Programa para Windows que envia a temperatura da CPU/GPU a um display USB de dois dígitos.
Documentação principal: `specification.md` (como funciona e deve funcionar),
`testing_guide.md` (testes obrigatórios), `BUILD.md` (build no Windows).

## Fluxo obrigatório para alterações no software

Todo pedido de alteração, correção ou funcionalidade nova passa pelos agentes locais em
`.claude/agents/`.

Responsabilidades (não se sobrepõem):

| Agente | Papel | Aprova/reprova |
|---|---|---|
| tadeu | Implementa e corrige | — |
| regis | Funcionalidade: testes automatizados, build e casos do guia (exceto `SEC-*`) | **Só funcionalidade** |
| luana | Segurança: segredos, vulnerabilidades, casos `SEC-*` do guia | **Só segurança** |
| patricia | Atualiza especificação e guia de testes | — |

Subagentes não chamam outros subagentes: **quem dispara cada etapa é a
conversa principal**, seguindo a linha `PRÓXIMO AGENTE` do relatório de cada agente.

```
pedido → tadeu → regis ──aprovado──→ luana ──aprovada──→ patricia → fim
           ↑       │                   │
           └───────┴───────────────────┘

  regis reprova (funcionalidade)  → volta direto ao tadeu; a luana não é chamada
  luana reprova (segurança)       → volta ao tadeu e reinicia: tadeu → regis → luana
```

1. **tadeu**: implementa. Passe o pedido completo do usuário (rodada 1) ou as falhas/achados a
   corrigir (rodadas seguintes, informando o número da rodada e a origem: regis ou luana).
2. **regis**: testa no Windows (automatizados, build e casos do guia). Passe o relatório
   completo do Tadeu e diga se o usuário autorizou ações que alteram o sistema.
   - `PRÓXIMO AGENTE: tadeu` → nova rodada do Tadeu com as falhas do Regis; depois volte ao
     passo 2. **A Luana não é chamada** enquanto o Regis não aprovar.
   - `PRÓXIMO AGENTE: luana` → passo 3.
3. **luana**: revisa segurança. Passe os relatórios do Tadeu e do Regis.
   - `PRÓXIMO AGENTE: tadeu` → nova rodada do Tadeu com os achados da Luana, e **o fluxo
     reinicia do começo**: Tadeu → Regis → Luana.
   - `PRÓXIMO AGENTE: patricia` → passo 4.
4. **patricia**: documenta. Passe os relatórios finais do Tadeu, do Regis e da Luana.
5. Ao final, informe ao usuário:
   - o que mudou;
   - os vereditos do Regis e da Luana;
   - o que a Patricia documentou;
   - a lista de **testes PENDENTE-MANUAL** que ele precisa executar.

**Interrupções:**

- Qualquer agente com `PRÓXIMO AGENTE: usuario` (3 reprovações seguidas do mesmo revisor) →
  pare e mostre o impasse ao usuário.
- Se o Tadeu chegar à **6ª rodada** na mesma tarefa, somando Regis e Luana → pare e peça
  orientação ao usuário.
- Se o Tadeu apontar conflito com a especificação → pergunte ao usuário antes de seguir.

Não pule etapas, não edite o código diretamente e não marque nada como aprovado sem o Regis e
a Luana. Perguntas e explicações que não alteram arquivos não precisam do fluxo.

## Regras do projeto

- O nome do software é **Fervura**, o esquema arquitetural do projeto está descrito só na
  `specification.md`.
- Ambiente: Windows, com o Git Bash como terminal do Claude Code. Python do projeto:
  `py -3.12`. Build: `cmd //c build.bat`.
- Ações que alteram o sistema (instalar ou desinstalar o Fervura ou o PawnIO, sair da conta,
  editar o registro) só com autorização explícita do usuário.
