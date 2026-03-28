# AI PR Review 项目架构

## 1. 系统总览

```mermaid
flowchart TB
    subgraph Client["前端 (static/index.html)"]
        UI[Web UI]
    end

    subgraph API["FastAPI 路由层"]
        GiteeRouter["/api/gitee/*"]
        ReviewRouter["/api/review"]
        RagRouter["/api/rag/*"]
    end

    subgraph Services["服务层"]
        GiteeSvc[gitee 服务]
        ReviewSvc[review 服务]
        RagSvc[rag_store]
        EnrichSvc[context_enrichment]
        EmbeddingSvc[embedding]
        PyrightSvc[pyright_analyzer]
        TreesitterSvc[treesitter_analyzer]
        SymbolSvc[symbol_graph]
    end

    subgraph External["外部依赖"]
        GiteeAPI[Gitee API]
        DashScope[通义千问 / DashScope]
        Kimi[Kimi API]
        PG[(PostgreSQL + pgvector)]
    end

    UI --> GiteeRouter
    UI --> ReviewRouter
    UI --> RagRouter

    GiteeRouter --> GiteeSvc
    GiteeRouter --> EnrichSvc
    GiteeRouter --> PyrightSvc
    GiteeRouter --> SymbolSvc

    ReviewRouter --> ReviewSvc
    ReviewRouter --> EmbeddingSvc
    ReviewRouter --> RagSvc

    RagRouter --> RagSvc

    GiteeSvc --> GiteeAPI
    ReviewSvc --> DashScope
    ReviewSvc --> Kimi
    ReviewSvc --> RagSvc
    RagSvc --> PG
    SymbolSvc --> PG
    EmbeddingSvc --> DashScope
```

---

## 2. 核心数据流

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as 前端
    participant G as Gitee Router
    participant E as 上下文增强
    participant R as Review 服务
    participant L as LLM

    U->>F: 输入 PR 链接 + Token
    F->>G: POST /api/gitee/fetch-pr
    G->>G: 拉取 diff + 变更文件
    opt enrich_context
        G->>E: 测试关联 + import 关联
    end
    opt use_pyright
        G->>E: Pyright 诊断
    end
    opt use_symbol_graph
        G->>E: Symbol Graph 扩展
    end
    G-->>F: diff, file_contexts, owner, repo

    F->>R: POST /api/review (diff, file_contexts, ...)
    alt 4 维度串行
        R->>R: Phase0: PR Summary (Pyright+RAG+PR)
        loop 4 维度
            R->>L: 正确性/安全/质量/依赖
            L-->>R: comments
        end
        R->>R: 按 (file, line±3) 合并
    else 三阶段
        R->>L: Pass1 预筛选
        R->>L: Pass2 主审查
        R->>L: Pass3 深化 Critical
    else 单次
        R->>L: 单次审查
    end
    L-->>R: JSON comments
    R-->>F: comments
    F-->>U: 展示 + 发送到 PR
```

---

## 3. 4 维度串行审查 pipeline

```mermaid
flowchart LR
    subgraph Phase0["Phase 0: Summary"]
        P0_IN[Pyright + RAG + PR]
        P0_LLM[LLM]
        P0_OUT[PR Summary]
        P0_IN --> P0_LLM --> P0_OUT
    end

    subgraph Phase1_4["Phase 1-4: 4 维度串行"]
        D1[正确性与边界]
        D2[安全]
        D3[质量]
        D4[依赖与并发]
    end

    subgraph Merge["合并"]
        M[按 file, line±3 聚合]
    end

    P0_OUT --> D1 --> D2 --> D3 --> D4 --> M
    D1 -.-> |diff + file_contexts + Summary| D1
```

---

## 4. 上下文增强流程

```mermaid
flowchart TB
    subgraph Input["输入"]
        PR[PR 元数据]
        Files[变更文件列表]
        Diff[diff]
    end

    subgraph Enrich["上下文增强 (可选)"]
        Test[测试文件关联<br/>test_* / *_test.py]
        Import[Import 关联<br/>Python ast / JS 正则]
        TS[Tree-sitter<br/>变更类型 + 符号]
        Pyright[Pyright<br/>类型诊断]
        Symbol[Symbol Graph<br/>caller/callee]
        Semantic[语义检索<br/>diff → Top-5 片段]
    end

    subgraph Output["输出"]
        FC[file_contexts<br/>Dict[path, content]]
    end

    PR --> Test
    Files --> Test
    Files --> Import
    Files --> TS
    TS --> Pyright
    Files --> Symbol
    Diff --> Semantic
    FC --> Semantic

    Test --> FC
    Import --> FC
    Pyright --> FC
    Symbol --> FC
    Semantic --> FC
```

---

## 5. 目录结构

```
prreview/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── routers/
│   │   ├── gitee.py         # fetch-pr, post-comment
│   │   ├── review.py        # 统一审查入口
│   │   └── rag.py           # RAG index / search
│   ├── services/
│   │   ├── gitee.py         # Gitee API 封装
│   │   ├── review.py        # LLM 审查、4 维度、三阶段
│   │   ├── context_enrichment.py  # 测试/import 关联
│   │   ├── embedding.py     # 语义检索
│   │   ├── pyright_analyzer.py
│   │   ├── treesitter_analyzer.py
│   │   ├── symbol_graph.py  # PostgreSQL caller/callee
│   │   └── rag_store.py     # pgvector 索引与检索
│   └── storage/
│       ├── db.py            # 数据库连接
│       ├── init_db.py       # 建表
│       ├── models.py        # Symbol Graph 模型
│       └── rag_models.py    # RAG 模型
├── static/
│   └── index.html           # 前端
├── scripts/
│   ├── run_review_and_report.py   # 真实 PR 审查 + 报告
│   ├── test_review_local.py       # 本地模拟审查
│   └── compute_catch_ratio.py     # Catch 比率计算
├── docs/
│   └── architecture.md      # 本文档
└── requirements.txt
```

---

## 6. 审查模式对比

| 模式 | 触发条件 | LLM 调用次数 | 特点 |
|------|----------|--------------|------|
| 单次 | `use_dimension_review=false` | 1 | 快，成本低 |
| 三阶段 | `use_multipass=true` | 2-3 | Pass1 预筛选 → Pass2 主审 → Pass3 深化 Critical |
| 4 维度 | `use_dimension_review=true`（默认） | 5 (1 Summary + 4) | Phase0 Summary → 4 维度串行 → 合并 |
