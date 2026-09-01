## Context

gh-search 项目当前位于 `plugins/gh-search/` 目录下，是一个 GitHub 开源项目智能搜索系统。项目包含：
- REST API 服务层（FastAPI）
- CLI 脚本链（按流程阶段组织）
- 数据库和日志文件
- 配置文件
- Claude Code 技能定义

项目需要迁移到 `agent-plugins/gh-search/` 子目录，以更好地组织和管理。迁移将按照之前设计文档中确定的目录结构进行。

## Goals / Non-Goals

**Goals:**
- 将项目从 `plugins/gh-search/` 迁移到 `agent-plugins/gh-search/`
- 保留原始目录不变
- 按照设计文档中的结构组织代码：`service/`、`scripts/`、`data/`、`config.yaml`、`SKILL.md`
- 更新所有代码中的路径引用
- 全面验证所有功能是否正常工作

**Non-Goals:**
- 不删除原始 `plugins/gh-search/` 目录
- 不修改项目的核心功能
- 不添加新功能
- 不改变项目的外部接口

## Decisions

### D1: 迁移策略 - 复制而非移动

**选择**：将项目从 `plugins/gh-search/` 复制到 `agent-plugins/gh-search/`，保留原始目录

**理由**：
- 用户明确要求保留原目录
- 降低风险：如果新目录有问题，可以回退到原始目录
- 便于比较新旧目录的差异

**备选方案**：
- A) 直接移动 → 否定：用户要求保留原目录
- B) 创建符号链接 → 否定：可能引起路径混淆

### D2: 目录结构 - 按设计文档组织

**选择**：按照之前设计文档中的结构组织代码

```
agent-plugins/gh-search/
├── service/          REST 服务（FastAPI）
├── scripts/          CLI 脚本链
│   ├── _common/      共享基础设施
│   ├── data/         Phase 1 数据采集
│   ├── pipeline/     Phase 2 批量嵌入
│   ├── search/       Phase 4 在线查询
│   ├── eval/         Phase 3 效果评估
│   └── maintenance/  Phase 5 运维
├── data/             数据库 + 日志
├── config.yaml       运行时配置
└── SKILL.md          Claude Code 技能
```

**理由**：这是之前团队讨论并确定的结构，按流程阶段分组，符合用户心智模型。

### D3: 路径更新策略 - 全面更新

**选择**：更新所有代码中的路径引用，包括import语句、配置文件路径等

**理由**：
- 确保在新位置能正常工作
- 避免路径混淆和运行时错误
- 保持代码一致性

**备选方案**：
- A) 只更新关键路径 → 否定：可能导致部分功能失效
- B) 不更新路径 → 否定：代码在新位置无法正常工作

### D4: 验证策略 - 全面验证

**选择**：迁移后全面验证所有功能，包括CLI脚本、REST API、数据库连接等

**理由**：
- 确保迁移后功能完整性
- 及时发现和修复问题
- 保证用户体验一致

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 路径更新不完整导致功能失效 | 系统性地搜索和更新所有路径引用 |
| 数据库文件路径错误 | 验证所有数据库连接和文件路径 |
| 配置文件路径问题 | 检查所有配置文件中的路径引用 |
| CLI脚本无法正常运行 | 逐个测试所有CLI脚本 |
| REST API服务启动失败 | 验证服务配置和依赖项 |

## Migration Plan

1. **准备阶段**：
   - 备份原始 `plugins/gh-search/` 目录（可选，用户选择不备份）
   - 创建 `agent-plugins/gh-search/` 目录

2. **复制阶段**：
   - 复制所有文件和目录到新位置
   - 保持目录结构完整

3. **重组阶段**：
   - 按照设计文档重组目录结构
   - 移动文件到正确位置

4. **更新阶段**：
   - 更新所有import语句
   - 更新配置文件中的路径引用
   - 更新SKILL.md中的路径引用

5. **验证阶段**：
   - 测试所有CLI脚本
   - 测试REST API服务
   - 验证数据库连接
   - 检查配置文件加载

6. **清理阶段**：
   - 确认所有功能正常
   - 更新文档

## Open Questions

- 无现有ADR需要supersede
- 是否需要更新.gitignore文件以排除新目录中的临时文件？
- 是否需要更新CI/CD配置（如果存在）？