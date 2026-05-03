# PikDrop 🚀

**PikPak → Dropbox** transfer tool with a real file browser.

## Features
- Login com email/senha PikPak **ou** Bearer token
- Browser de arquivos com navegação por pastas
- Selecionar múltiplos arquivos
- Transferência com progresso em tempo real e log ao vivo
- Upload em chunks para arquivos grandes (>150 MB)
- Deploy Railway em 1 clique

## Deploy no Railway

1. Crie um repo no GitHub com estes arquivos
2. Em [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Adicione variável de ambiente: `SECRET_KEY=qualquer_string_aleatoria`
4. Deploy! URL pública gerada automaticamente

## Rodar localmente

```bash
pip install -r requirements.txt
python app.py
# Abra http://localhost:5000
```

## Como usar

1. **PikPak**: login com email/senha ou cole o Bearer token
2. **Dropbox**: cole o token (lembre de marcar `files.content.write` + `files.content.read` ANTES de gerar o token)
3. Navegue pelas pastas do PikPak, selecione os arquivos
4. Clique **Iniciar Transferência**
