SELECT 'CREATE DATABASE prefect'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prefect')\gexec
SELECT 'CREATE DATABASE langgraph'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langgraph')\gexec
