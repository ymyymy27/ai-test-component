# Python 嵌入示例

```python
from ai_test.interfaces.python_api import AITestAPI

api = AITestAPI(".aitest")
project = api.create_project("demo", "演示项目")
print(project)
```

