# C1.6 — Ejecución A2 de la rotación TLS

07/11/2026. **Resultado A2: PASSED.** Horas en Europe/Madrid.

## Preflight y sustitución

- A las 15:54:31 quedaban 24 h 35 min 44 s hasta la caducidad de la hoja
  anterior (08/11/2026 16:30:15). GO con la reserva acordada de dos horas.
  Keycloak estaba saludable, servía la hoja anterior por TLS 1.3 y discovery y
  JWKS respondían por TLS estricto. FastAPI y Streamlit estaban parados. No
  había listeners en 8000/8501 ni procesos Python del piloto. El flujo Business
  revisado atiende el análisis en la solicitud y los trabajos de la UI se
  mantienen en memoria. Ambos indicadores de aprobación estaban en falso.
- Coincidieron los cinco hashes de staging, los cinco del respaldo A1 y los
  cinco activos. Se comprobó la recuperación de los pares de claves del
  respaldo mediante firma y verificación de retos.
- El operador importó la CA pública nueva en el almacén de confianza del perfil
  Chrome Default, cotejó su SHA-256 y confirmó que conservó la CA anterior.
- Se omitió la parada de los consumidores ya parados. Tras detener Keycloak
  se guardaron los cinco archivos activos en el respaldo privado
  `backup-a2-20261107T145640Z`, bajo el directorio A1. Se cotejaron con el
  manifiesto A1. Se escribieron los cinco archivos nuevos en sus rutas físicas
  existentes, se cotejaron con el manifiesto A1.1, se comprobó que las ACL
  permanecieron iguales y se verificó lectura como UID 1000 con el montaje
  de solo lectura. Se arrancó el mismo contenedor de Keycloak.

## Certificados y verificaciones

| Certificado activo | SHA-256 | Caducidad UTC |
| --- | --- | --- |
| CA A1.1 | `b3933c69d2358a0b06cfdfb63dfc88cbed6d8d546c8359476ce1d166a5ba26db` | 07/11/2027 14:25:35 |
| Hoja Keycloak | `0e834970d0230704297a308039a6211061651eac6e6a62acf42289e6417528e0` | 05/02/2027 14:25:35 |

- Keycloak saludable en 127.0.0.1:8443. La huella servida coincide con la
  nueva hoja. TLS 1.3 y cadena verificada con la nueva CA para `localhost`
  y `127.0.0.1`. Discovery 200, issuer exacto y JWKS 200 con el mismo hash
  canónico antes y después, `067c1b0c428e3ae19f6810bc488dadbfc84e99fed6105b50d9ab5e18a840d6ac`.
- FastAPI arrancó en 127.0.0.1:8000, verificó JWKS con su CA explícita y
  conservó sus raíces públicas. GET `/health` y `/ready` devolvieron 200.
  GET `/api/v1/session` sin credenciales devolvió 401.
- Streamlit arrancó en modo sesión en 127.0.0.1:8501 con `SSL_CERT_FILE`
  apuntando al PEM activo para el cliente Authlib. GET `/_stcore/health`
  devolvió 200. El estado de los tres listeners se confirmó a las 15:58:39.
- Se conservaron la CA antigua en Chrome y ambos respaldos. Ningún POST
  Business durante la intervención.
- Hubo un rechazo inicial al iniciar sesión. Los eventos de Keycloak
  registraron la categoría `user_not_found`. El operador confirmó después
  en Chrome Default la conexión sin alertas TLS, el login con sesión HTTP
  200, el logout correcto y la ausencia de resultados Business heredados.
  La recuperación del login no requirió cambios de configuración ni rollback.
- A las 16:04:48 se confirmó de nuevo el certificado servido, TLS 1.3,
  Keycloak saludable, los endpoints de salud y disponibilidad de FastAPI y
  Streamlit con 200, y `approved=false` y `approved_post=false`.
