<div align="center">

# static-site-graph

声明式静态网站图谱建模框架 (Declarative Static Site Graph Modeling Framework)

[![Python](https://img.shields.io/badge/Python-3.10+-FFD43B.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Pytest](https://img.shields.io/badge/Testing-Pytest-green.svg)](https://docs.pytest.org/en/latest/)

</div>

---

## 📖 项目定位 (The Vision)

`static-site-graph` 是一个高度抽象、配置驱动的**通用静态网站图谱爬取与结构化建模引擎**。它旨在将传统的、基于网页的半静态内容站点，转化为完全结构化、可审计且语义友好的镜像索引数据图谱 (Site Graph)。

**它不是一个业务爬虫，更不是一个简单的网页下载器。** 它是整个搜索基建体系中的**上游数据源引擎**。通过提供声明式的领域特定配置 (DSL)，它可以将杂乱无章的导航树、分页列表、详情页和附件库剥离出来，输出供下游搜索产品（如 `njupt-search`）消费的绝对结构化数据源。

---

## ✨ 核心特性矩阵 (Core Features)

*   **声明式爬取建模 (Schema-Driven Crawler)**
    抛弃了硬编码的 CSS 选择器逻辑，全量采用 YAML 配置驱动。站点的拓扑模型（如导航、专栏、详情页）被严格抽象，框架根据模型自动执行图谱遍历。
*   **强确定性与绝对审计 (Deterministic & Auditable)**
    引擎强制执行最严苛的 URL 漏斗审计规则。爬虫发现的任何一条 URL，无论是被跳过、下载失败、作为附件处理，还是属于未定义的脏路径，都必须进行全量的 Manifest 归档登记，确保**零 URL 遗漏**。
*   **原生无头浏览器集成 (Headless Browser Ready)**
    针对 HTTP Fetch 获取到的脏 DOM（包含前端动态渲染的页面），引擎内置并强制对“具有代表性的页面家族”进行 Chrome 无头浏览器双向校验。
*   **分类优先原则 (Classification-First Architecture)**
    强制规定：任何一个页面在提取内容之前，必须先经过严谨的 Page Type 路由分类。

---

## 🏗️ 核心建模拓扑 (Modeling Topology)

一个站点被数学化地建模为以下结构：

```text
site
  ├── navigation tree (导航树)
  ├── sections / columns (专栏区块)
  ├── list pages (列表聚合页)
  ├── pagination pages (分页路由)
  ├── detail pages (内容详情页)
  ├── attachment metadata (附件元数据)
  ├── external links / systems (外部出链)
  ├── edge graph (图谱边缘节点)
  └── manifest + audit report (构建清单与审计报告)
```

---

## 🚀 开发者指南 (Developer Guide)

### 仓库边界 (Repository Boundary)

为了保证底层框架的绝对纯洁性，本仓库**仅追踪核心框架源码、契约 Schema、测试用例以及通用示例**。所有的下游实例特定配置（如特定学校或站点的抓取规则）、本地调试日志、Agent 运行记录均被 `.gitignore` 强行隔离。**绝对禁止将任何实例相关的硬编码（如某大学教务处地址）带入本框架核心。**

### 本地部署 (Installation)

```bash
# 以开发模式安装核心包及其测试依赖
python -m pip install -e .[dev]

# 查阅全局 CLI 工具箱能力
python -m sitegraph.cli --help
```

### 标准化工作流 (Standard Workflow)

1. 使用开发者工具探查目标站点的结构规律。
2. 将站点的路由模型、分页模式编码进 `site.yaml` 声明式配置文件中。
3. 运行配置驱动的图谱爬虫，进行页面发现与结构萃取。
4. 导出符合标准的结构化镜像索引包 (Mirror Index)。
5. 生成 Manifest 清单，向游消费者派发数据。

---

## 📄 许可证 (License)

本项目开源协议基于 [AGPL-3.0 License](LICENSE)。
