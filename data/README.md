# data/

Aquí va el **parquet maestro** del modelo geoespacial:

```
data/master_variables.h3.parquet
```

- Lo entrega el **equipo de investigación**. NO se versiona (gitignored).
- El backend lo carga en memoria al arrancar (warmup) — ver `shared/config.py` (`PARQUET_PATH`).
- Para usar otro path o nombre: variable de entorno `PARQUET_PATH`.
