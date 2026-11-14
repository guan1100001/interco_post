@echo off
chcp 65001 >nul
set "DB=D:\workbuddy\qddev\interco_ontology.db"

echo 准备清理数据库：%DB%
:: 示例：删除日志表中旧数据
sqlite3 "%DB%" "DELETE FROM accounting_documents;DELETE FROM audit_log;DELETE FROM interco_events;DELETE FROM invoice_items;DELETE FROM invoices;DELETE FROM pipeline_sessions;DELETE FROM posting_events;DELETE FROM accounting_documents;"
echo 删除完成
:: 可选：回收空间
sqlite3 "%DB%" "VACUUM;"
echo VACUUM 完成
pause