# Swagger UI 本地资源

当前版本：官方 `swagger-ui-dist@5.33.1`。用于服务端独立提供文档，不进入Vite业务包；无需安装npm依赖即可运行Python API文档。

来源：`https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.1.tgz`。
发布包integrity已与npm官方元数据核对，记录于版本目录的`manifest.json`，另记录各文件SHA-256。仅取bundle、CSS、favicon和许可证；保留官方文件原始字节、LICENSE、NOTICE及bundle第三方许可清单。

`.gitattributes`禁用版本目录文件的换行转换，确保Windows检出后的官方字节及校验值一致。

选择5.x是为了渲染FastAPI生成的OpenAPI 3.1。之前Python `swagger-ui-bundle==1.1.0`实际携带UI 4.15.5，会显示“Unable to render this definition”。兼容范围见[官方版本表](https://github.com/swagger-api/swagger-ui#compatibility)。

更新时从官方registry获取固定版本发布包，核对integrity，按以上文件白名单复制到新的版本目录并生成校验清单；不要直接解压整个归档或覆盖旧版本文件。同步修改api_docs.py版本、渲染测试版本、文档及许可证。资源URL包含版本，文档HTML使用no-store，避免缓存混用。

验证必须包含`npm run test:api-docs`（在frontend执行）：真实FastAPI OpenAPI 3.1交给实际Swagger bundle，在隔离DOM中显示全部操作且无版本错误；同时运行后端Schema/文件校验及`python -m scripts.smoke_api_docs`代理检查。HTTP 200不能单独作为渲染成功依据。
