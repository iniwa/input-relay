# CLAUDE.md

> Detailed notes (Japanese): CLAUDE_ja.md

## Communication
- Write efficient code. Keep dependencies minimal; lightweight alternatives preferred.
- **If the target PC is not specified, ask the user whether to run on Main PC or Sub PC before proceeding.**

## Environments

### Main PC
| Item | Detail |
|------|--------|
| CPU | Ryzen 7 9800X3D |
| GPU | RTX 4080 — CUDA available |
| RAM | 48GB |
| OS | Windows 11 |
| IP | 192.168.1.210 |

### Sub PC
| Item | Detail |
|------|--------|
| CPU | Ryzen 9 5950X |
| GPU | RTX 5060 Ti — 16GB VRAM, CUDA Compute 8.9 (sm_89) |
| RAM | 64GB |
| OS | Windows 11 |
| IP | 192.168.1.211 |

## AI / ML Development
- **Purpose**: AI / ML tools, run locally — no packaging or distribution needed
- **Language**: Python is default for ML tasks
- **GPU**: CUDA available on both PCs. Use GPU-accelerated libraries where beneficial
- **Env**: Avoid virtual environments whenever possible; install packages globally. Use `venv`/`uv` only when dependency conflicts make it unavoidable. Never use conda unless a dependency strictly requires it.

## General Tool Development
- **Purpose**: Utility scripts, automation, general-purpose tools
- **Language**: No fixed preference; pick the simplest fit for the task
- **Env**: Same as above — global install preferred, venv only when needed

## Tooling
- Use **Serena MCP** tools for code navigation and editing to maximize efficiency (symbol search, overview, replace, insert, etc.)

## Code Style
- Prefer minimal, readable code over abstraction-heavy patterns
- Avoid heavyweight frameworks for simple tasks
- No CI/CD, installers, or packaging unless explicitly requested
