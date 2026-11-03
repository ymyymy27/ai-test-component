# A包 本地核心底座 施工计划 LU
## 一、模块清单（A包负责）
1. contracts 协议定义，遵循 aitest.local/2.0
2. application/ports.py 端口、命令、事件、错误定义
3. infrastructure/file_store 存储层：单写锁、不可变记录、备份、完整性检查
4. interfaces/local/api.py 本地路由、生命周期
5. bootstrap.py 工作空间实例装配，注册B/C/D用例

## 二、不负责范围
- 不生成测试计划、不执行业务被测代码、不写Trae前端页面

## 三、开发顺序
1. 阅读总体架构文档，梳理接口定义
2. 编写A包对外接口文档，放到docs/接口对接/
3. 实现contracts协议与ports端口定义
4. 实现file_store存储层（含锁、不可变记录、备份）
5. 实现api.py与bootstrap装配逻辑
6. 编写假存储、测试夹具，给B/C/D并行开发使用
7. 按照【独立验收】清单自测

## 四、验收项
- 单写锁、同意图去重
- 崩溃恢复（写入前后）
- 不可变历史、备份
- 索引分页，禁止全扫查询
- 存储不可写异常处理
