# AI Code Review SaaS 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 aiprreview 升级为 FastAPI 后端，实现上下文感知审查、Kimi/DashScope 双 LLM、结构化输出与前端 severity 筛选。

**Architecture:** FastAPI + 模块化 routers/services；fetch-pr 拉取 diff + 变更文件完整内容；统一 /api/review 支持多 LLM；JSON 优先解析。

**Tech Stack:** Python 3.11+, FastAPI, httpx/requests

---

## Task 1: FastAPI 骨架 + Gitee API 迁移 ✅

## Task 2: fetch-pr 拉取变更文件完整内容 (L1+L2) ✅

## Task 3: 统一 /api/review + Kimi/DashScope ✅

## Task 4: 升级 Prompt + JSON 双解析 ✅

## Task 5: 前端 severity 展示与筛选 ✅

## Task 6: Docker + 部署文档 ✅
