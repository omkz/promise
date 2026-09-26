FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are inlined into the client bundle at `next build` time, not read at
# container-start time -- without these build ARGs, a production image silently ships
# whatever web/.env.local happens to contain (localhost, for local dev) baked in forever.
# Pass the real values via `docker build --build-arg` at deploy time.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_AUTH_MODE=local
ARG NEXT_PUBLIC_DEV_USER_ID=usr_dev_default
ARG NEXT_PUBLIC_DEV_WORKSPACE_ID=ws_dev_default
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_AUTH_MODE=$NEXT_PUBLIC_AUTH_MODE \
    NEXT_PUBLIC_DEV_USER_ID=$NEXT_PUBLIC_DEV_USER_ID \
    NEXT_PUBLIC_DEV_WORKSPACE_ID=$NEXT_PUBLIC_DEV_WORKSPACE_ID
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=deps /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "run", "start"]
