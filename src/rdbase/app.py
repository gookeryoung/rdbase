"""rdbase Web 应用（FastAPI）."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="rdbase", description="科研数据库。")


@app.get("/")
def read_root() -> dict[str, str]:
    """根路径健康检查."""
    return {"status": "ok", "project": "rdbase"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("rdbase.app:app", host="0.0.0.0", port=8000, reload=True)
