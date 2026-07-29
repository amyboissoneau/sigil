FROM python:3.13-slim
WORKDIR /app
COPY sigil/ sigil/
COPY public/ public/
# State lives in one SQLite file; mount a volume at /data to persist the world.
ENV SIGIL_DB=/data/sigil.db
RUN mkdir -p /data
VOLUME /data
EXPOSE 8383
CMD ["python3", "-m", "sigil.server"]
