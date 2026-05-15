FROM node:20-alpine

WORKDIR /app

# Install dependencies first for layer caching
COPY package*.json ./
RUN npm ci --only=production

COPY src/ ./src/

CMD ["node", "src/index.js"]
