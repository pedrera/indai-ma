# C1.6 — A1.1 TLS y procedimiento A2

07/11/2026. Cadena preparada y validada en staging privado. A2 pendiente de
confirmar la confianza de Chrome y autorizar la ventana operativa.

## Preflight y resultado

- Dos handshakes verificados con la CA activa y el nombre `localhost`, antes y
  después de generar la cadena, negociaron TLS 1.3 en `127.0.0.1:8443` y devolvieron
  la huella antigua `85b8db4841eb48b9d01212f141e189a67cadae829d9d5e3fb7dfe363e2e909dd`.
- Hoja activa vigente hasta el **08/11/2026 a las 16:30:15** en Madrid
  (15:30:15 UTC). La CA activa, de huella
  `f33ae7b5c99a565b1957eec4a0fa31c0dd0d50b75d573ef0b5b3aa8ed9043a7b`,
  vence el 24/11/2026 a las 16:30:15 en Madrid (15:30:15 UTC).
- Keycloak 26.8.1 saludable, UID 1000, solo 8443 publicado en loopback,
  `KC_HTTP_ENABLED=false`, hostname `https://localhost:8443` y montaje físico
  de `tls` en `/opt/keycloak/conf/dev-tls` de solo lectura. Los dos parámetros
  HTTPS apuntan al PEM de cadena y al PKCS8 de servidor de ese montaje.
- Los cinco archivos activos y las cinco copias A1 coinciden con el manifiesto
  original SHA-256. Ambas claves de respaldo firmaron un reto verificado con sus
  certificados. FastAPI conserva la referencia explícita a `ca/ca-cert.pem` en
  `backend.env`. La referencia `SSL_CERT_FILE` de Streamlit consta en el
  inventario A1 y deberá confirmarse al arrancar. En la consulta de listeners
  solo apareció 8443, de modo que debe verificarse el estado de API y UI al
  comienzo de la ventana. `approved=false` y `approved_post=false` confirmados
  en el TOML privado. Ningún POST Business.

## Material para A2

Usar exclusivamente
`C:\Users\pedre\indai-ma-tls-rotation-a1-20261031\staging-a1.1-20261107`.
El respaldo y el staging A1 conservan sus rutas y contenido. Las nuevas claves
PKCS8 heredan acceso limitado a pedre, SYSTEM y Administradores. La CA privada
permanece fuera del montaje de Keycloak.

| Certificado | Vigencia desde 07/11/2026 14:25:35 UTC | SHA-256 |
| --- | --- | --- |
| CA RSA3072 | 365 días, hasta 07/11/2027 14:25:35 UTC | `b3933c69d2358a0b06cfdfb63dfc88cbed6d8d546c8359476ce1d166a5ba26db` |
| Servidor RSA2048 | 90 días, hasta 05/02/2027 14:25:35 UTC | `0e834970d0230704297a308039a6211061651eac6e6a62acf42289e6417528e0` |

Las 16 comprobaciones de A1 pasaron. El `validation-summary.json` privado
contiene cada resultado y el manifiesto SHA-256 de staging. Se verificaron
respaldo utilizable, claves frescas también respecto de A1, seriales aleatorios
positivos y distintos, firmas, restricciones críticas de CA y hoja, usos de
clave, EKU serverAuth, SAN DNS localhost e IP 127.0.0.1, vigencias anidadas,
RSA/SHA-256 y correspondencia de ambos pares en disco. OpenSSL cargó la cadena
PEM y la clave PKCS8. TLS 1.3 mediante MemoryBIO verificó ambos SAN, rechazó la
CA anterior y rechazó un hostname incorrecto con la CA nueva. Los cinco hashes
activos permanecieron iguales. La lectura con UID 1000 y la carga de la nueva
cadena por Keycloak se validarán durante A2.

## Chrome y consumidores de la CA antigua

El inventario actual muestra `Default` como único perfil registrado bajo
`Google\Chrome\User Data`. El proceso principal no declaró un perfil o
directorio alternativo ni las opciones de omisión TLS buscadas. Esto no vincula
el perfil con las pruebas OIDC históricas. La huella antigua no apareció en
`Root`, `CA`, `TrustedPeople` ni `Disallowed` de `CurrentUser` o
`LocalMachine`. La fuente efectiva del perfil histórico sigue pendiente.

En la ventana del perfil usado para OIDC, comprobar `Profile Path` en
`chrome://version`. Revisar `chrome://certificate-manager` y
`chrome://policy` en esa misma ventana. Localizar la huella antigua en las
secciones de confianza local, plataforma y certificados administrados.
Comprobar el HTTPS de Keycloak sin iniciar sesión ni continuar tras alertas.
Registrar huella, origen y alcance de la confianza. Si se confirma un almacén
local con el alcance aprobado, durante A2 importar solo
`staging-a1.1-20261107\ca\ca-cert.cer` en la sección de certificados de
confianza de ese almacén, cotejar la huella y conservar la entrada anterior.
Si el perfil, la fuente o el alcance siguen sin confirmarse, o la interfaz
redirige a un almacén Windows de alcance no autorizado, detener A2 antes de
cualquier cambio. Chrome puede considerar certificados administrados
localmente además de sus raíces integradas.
[Documentación de Chromium](https://blog.chromium.org/2022/09/announcing-launch-of-chrome-root-program.html)

Dependencias conocidas o históricas a verificar antes de retirar la CA antigua:
el HTTPS de Keycloak, FastAPI por
`INDAI_OIDC_CA_FILE`, Streamlit/Authlib por `SSL_CERT_FILE`, el perfil Chrome
del piloto y scripts locales que lean el PEM o conserven copias o huellas
fijadas. Conservar la confianza antigua hasta cerrar la recuperación y revisar
los consumidores que compartan su almacén.

## A2 — secuencia operativa pendiente

1. Autorizar ventana, alcance de confianza y prueba OIDC. Confirmar la puerta de
   Chrome anterior. Verificar ambos flags en falso, ausencia de trabajos,
   identidades y estado de servicios, horas de host y contenedor, validez,
   hashes, permisos y respaldo. Anotar una hora de decisión que deje margen
   suficiente para restaurar y verificar antes de las **16:30:15 del 08/11**.
   Si no hay margen, exigir una decisión explícita sobre operar sin rollback
   válido antes de proceder.
2. Preparar referencias a los cinco archivos verificados y revisar rutas
   físicas, ACL y propietario. Confirmar la confianza explícita del API y de
   Streamlit en el mismo PEM público activo y conservar las raíces públicas
   de FastAPI. Confirmar el mecanismo de importación Chrome antes de la parada.
3. Detener ordenadamente Streamlit, FastAPI y finalmente solo Keycloak,
   omitiendo servicios ya parados tras comprobarlo. Con los consumidores
   detenidos, sustituir únicamente `ca/ca-cert.pem`, `ca/ca-cert.cer`,
   `ca/ca-key.pem`, `tls/localhost-cert.pem` y `tls/localhost-key.pem`.
   Verificar los cinco hashes contra el manifiesto A1.1, pares, ACL y rutas
   antes de arrancar. Mantener los volúmenes, montaje de solo lectura,
   configuración OIDC, clientes, membresías y claves de firma JWT.
4. Importar solo la CA pública nueva en la fuente Chrome aprobada y cotejar su
   huella. Mantener la confianza antigua. Arrancar únicamente Keycloak.
   Verificar lectura efectiva como UID 1000, salud, loopback y HTTPS, huella
   exacta nueva, cadena y hostname con la CA nueva, TLS 1.3, discovery con
   issuer exacto y JWKS accesible y coherente con el baseline.
5. Arrancar FastAPI con la configuración revisada. Confirmar JWKS por TLS
   verificado, salud y disponibilidad 200, y GET `/session` sin credenciales
   con 401. Arrancar Streamlit en modo sesión con `SSL_CERT_FILE` revisado.
   Desde el perfil confirmado, comprobar HTTPS sin advertencias, login
   nativo, GET `/session` 200, identidad y permisos esperados, selector
   vacío y ausencia de datos heredados. Validar logout nativo y nuevo login.
   Confirmar ambos flags en falso y mantener Business deshabilitado.
6. Registrar evidencia pública y aplazar la retirada de la CA antigua y la
   limpieza de claves hasta cerrar la ventana y revisar dependencias.

Detener ante un alcance de confianza inesperado, un hash o permiso discordante,
un fallo de salud, TLS, discovery o JWKS, o identidad/permisos inesperados.
Ante sustitución parcial, mantener los servicios detenidos hasta tener un
conjunto completo y verificado. Mientras quede margen y la cadena antigua siga
vigente, detener los servicios afectados, restaurar los cinco archivos de
`backup`, verificar hashes y ACL, y revertir solo la entrada Chrome nueva si
procede. Arrancar Keycloak, FastAPI y Streamlit en ese orden y repetir las
validaciones con la huella antigua. Si el certificado antiguo ha caducado o
caducará antes de terminar, mantener el servicio afectado fuera de servicio y
reparar la cadena nueva o emitir otra cadena válida bajo autorización operativa.
Mantener verificación TLS y HTTPS durante toda la recuperación.
