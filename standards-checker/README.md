# standards-checker

Verifica que un microservicio Java / Spring cumpla el estandar de arquitectura hexagonal (PR241standards, version 2)
y publica el resultado en el sheet de solo lectura. Las reglas estan en `criterios.yml` (30 criterios).

## Usar en un micro

En el `.gitlab-ci.yml` del micro (si no existe, crearlo con solo esto):

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/salvador-quispe/PR241-Arquitectura/main/standards-checker/templates/check-micro.yml'
```

Eso es todo: sin variables, sin copiar carpetas y sin tocar `stages`. En cada push a `develop` se revisa el micro y su
pestana en el sheet se actualiza. No bloquea el pipeline.

## Que hace
Lee la estructura del codigo (nombres de clases, paquetes e imports; nunca lo envia), la compara con `criterios.yml`
y envia el reporte al sheet. En GitLab toma el nombre del micro, la rama y el commit de las variables del pipeline.

## Cambiar una regla
Editar `criterios.yml` (texto, severidad, peso) o `checker/reglas.py` (logica) en este repo. Los micros usan la
version nueva en su siguiente push.
