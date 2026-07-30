rdbase
======

科研数据库。

.. toctree::
   :maxdepth: 2
   :caption: 目录

   api
   changelog

简介
====

科研数据库。。

安装
====

.. code-block:: bash

   pip install rdbase

或使用 uv_:

.. code-block:: bash

   uv add rdbase

.. _uv: https://docs.astral.sh/uv/

快速上手
========

.. code-block:: python

   import rdbase

   # TODO: 添加使用示例

开发
====

.. code-block:: bash

   # 安装开发依赖
   uv sync --extra dev

   # 运行测试
   uv run pytest

   # 类型检查
   uv run pyrefly check .

   # 代码风格
   uv run ruff check .
   uv run ruff format --check .
