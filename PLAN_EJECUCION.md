# FlipIQ — Plan de Ejecucion (16 semanas)

## Estado actual del proyecto

El backend tiene una **fundacion solida**: auth JWT, modelos de DB, calculadoras de fees para 4 marketplaces, motor de scoring heuristico, CRUD de watchlists, y 11 tests unitarios pasando.

**Sin embargo, el nucleo de valor esta ausente:** el precio estimado de venta siempre es `cost_price * 1.5` cuando el producto es nuevo. Los clientes de eBay y Amazon existen como modulos pero **no estan conectados** al motor de analisis. Sin datos reales de mercado, el producto es inutilizable.

### Lo que ya funciona
- Auth (registro, login, JWT)
- Modelos: User, Product, Analysis, Watchlist, WatchlistItem
- Calculadoras de fees: eBay (13.25%), Amazon FBA (15%+$3.50), MercadoLibre (16%), Facebook (5%)
- Scoring heuristico (flip_score, risk_score) y recomendacion buy/watch/pass
- Desglose de canales (calcula margen para 4 marketplaces)
- Sistema de creditos basico (decremento por analisis)
- CRUD completo de watchlists
- 11 tests unitarios

### Bloqueadores criticos
1. **Migracion de Alembic nunca ejecutada** — sin tablas en BD
2. **EbayClient existe pero no esta conectado** a `analysis_service`
3. **AmazonClient es placeholder** — todos los metodos devuelven `[]`
4. **velocity_score hardcodeado** a 50
5. **Sin datos reales de comps** (ventas completadas)

---

## Fase 0: Fundacion (Semana 1-2) — Discovery + Infrastructure

> **Objetivo:** BD operativa, datos reales fluyendo, app desplegable.

### 0.1 Infraestructura basica
- [ ] Ejecutar `alembic revision --autogenerate -m "initial"` + `alembic upgrade head`
- [ ] Crear `Dockerfile` + `docker-compose.yml` (app + PostgreSQL + Redis)
- [ ] Configurar CORS restrictivo para produccion (sacar `allow_origins=["*"]`)
- [ ] Forzar `SECRET_KEY` seguro en produccion (validacion en config.py)
- [ ] Agregar rate limiting basico (slowapi o similar)

### 0.2 Discovery con resellers (paralelo)
- [ ] Entrevistar 15-25 resellers reales
- [ ] Validar: plataformas que usan, pain points, willingness-to-pay
- [ ] Definir: mercado objetivo (EE.UU. vs LatAm), modelo de reseller prioritario
- [ ] Decidir: marketplaces must-have para MVP (Amazon/eBay/MercadoLibre)

### Entregable: BD con tablas creadas, Docker funcionando, insights de discovery

---

## Fase 1: Motor de Datos Reales (Semana 3-4) — El corazon del MVP

> **Objetivo:** Que un analisis devuelva precios basados en datos reales, no inventados.

### 1.1 Conectar EbayClient al motor de analisis
- [ ] Importar `EbayClient` en `analysis_service.py`
- [ ] En `_find_or_create_product()`: llamar a eBay Browse API para obtener listings
- [ ] Calcular `avg_sell_price` del producto basado en comps de eBay
- [ ] Manejar expiracion del token de eBay (actualmente se cachea sin refresh, expira en 2h)
- [ ] Implementar caching de resultados (Redis o en-memory) para reducir API calls

### 1.2 Comps de ventas completadas
- [ ] Evaluar opciones: eBay Finding API (legacy), Terapeak (requiere Store), o SerpAPI como fallback
- [ ] Implementar `get_sold_comps()` con datos reales (no listings activos como proxy)
- [ ] Calcular precio promedio, mediana, rango de precios de venta real

### 1.3 Enriquecimiento de producto
- [ ] Buscar titulo, brand, categoria, imagen via eBay cuando se ingresa barcode
- [ ] Persistir datos enriquecidos en tabla `products` para futuras consultas
- [ ] Implementar UPC/EAN lookup (considerar Open Food Facts, UPCitemdb como fuentes gratuitas)

### 1.4 Scoring con datos reales
- [ ] `velocity_score`: basado en numero de ventas/comps en ultimos 30/90 dias
- [ ] `risk_score`: incorporar varianza de precios de comps + saturacion de listings activos
- [ ] `flip_score`: ponderar margen + velocidad + riesgo

### Entregable: `POST /api/v1/analysis/` devuelve analisis basado en datos reales de eBay

---

## Fase 2: Integracion Amazon + Multi-canal (Semana 5-6)

> **Objetivo:** Soporte cross-market real (el diferenciador clave de FlipIQ).

### 2.1 Amazon SP-API
- [ ] Registrar como desarrollador en Amazon Developer Central
- [ ] Implementar `AmazonClient.search_by_barcode()` con Catalog Items API
- [ ] Obtener BSR (Best Sellers Rank) como proxy de velocidad de venta
- [ ] Obtener precio de Buy Box para estimacion de sale price
- [ ] Conectar al motor de analisis

### 2.2 Desglose cross-market mejorado
- [ ] En cada analisis, consultar precios reales en eBay + Amazon (no solo fees teoricos)
- [ ] Recomendar el mejor canal basado en datos reales (no solo menor fee)
- [ ] Mostrar "oportunidad de arbitrage" cuando hay diferencia significativa entre canales

### 2.3 Export CSV
- [ ] Endpoint `GET /api/v1/analysis/export?format=csv`
- [ ] Incluir: producto, costo, precio estimado, margen por canal, recomendacion
- [ ] Limitar a tiers Business y Power

### Entregable: Analisis cross-market eBay + Amazon con datos reales, export CSV

---

## Fase 3: Watchlist inteligente + Alertas (Semana 7-8)

> **Objetivo:** Crear habito recurrente con alertas y watchlists activas.

### 3.1 Watchlist con monitoreo de precios
- [ ] Job periodico (celery/arq) que re-evalua items en watchlists
- [ ] Detectar: precio bajo el target_buy_price, cambio significativo de precio
- [ ] Almacenar historial de precios por producto

### 3.2 Alertas email
- [ ] Integrar SendGrid (o transactional email similar)
- [ ] Templates: "Precio bajo tu target", "Nuevo analisis disponible", "Creditos bajos"
- [ ] Preferencias de notificacion por usuario (frecuencia, canales)

### 3.3 Push notifications (base)
- [ ] Implementar endpoint para registrar device tokens (Firebase Cloud Messaging)
- [ ] Push para: alertas de precio, creditos agotados

### Entregable: Watchlists que monitorean precios automaticamente + alertas email

---

## Fase 4: Pagos + Limites por Plan (Semana 9-10)

> **Objetivo:** Monetizacion funcional.

### 4.1 Integracion Stripe
- [ ] Crear productos y precios en Stripe (Free, Pro $19, Business $49, Power $99)
- [ ] Endpoint `POST /api/v1/billing/checkout` — genera Stripe Checkout session
- [ ] Webhook de Stripe para actualizar `tier` y `credits_remaining` del usuario
- [ ] Portal de Stripe para gestion de suscripcion (upgrade, downgrade, cancelar)

### 4.2 Enforcement de limites por tier
- [ ] Middleware o dependency que valide creditos segun tier:
  - Free: 20 analisis/mes
  - Pro: 400 analisis/mes
  - Business: 2,500 analisis/mes
  - Power: 10,000 analisis/mes
- [ ] Reset mensual automatico de creditos (cron job)
- [ ] Sistema de creditos extra para excedentes (pay-per-use)

### 4.3 Add-on SMS (Twilio)
- [ ] Alertas SMS como add-on de pago ($0.0083/SMS)
- [ ] Solo para tiers Business y Power

### Entregable: Pagos con Stripe, limites por tier enforceados, SMS premium

---

## Fase 5: IA + Trust Layer (Semana 11-12)

> **Objetivo:** Diferenciacion por inteligencia y transparencia.

### 5.1 IA para scoring avanzado
- [ ] Integrar Claude/OpenAI para generar resumen de analisis ("Por que comprar/pasar")
- [ ] Categorizar automaticamente productos nuevos
- [ ] Detectar patrones estacionales en datos de comps

### 5.2 Trust Layer
- [ ] Cada recomendacion muestra: fuentes de datos, supuestos, confidence level
- [ ] "Confidence score" visible: cuantos comps, antiguedad de datos, varianza de precios
- [ ] Desglose transparente de fees en cada canal

### 5.3 Feedback loop
- [ ] Endpoint `POST /api/v1/analysis/{id}/feedback` — usuario reporta si fue buen/mal flip
- [ ] Endpoint `POST /api/v1/analysis/{id}/result` — usuario registra precio de venta real
- [ ] Post-mortem automatico: comparar estimacion vs resultado real
- [ ] Ajustar scoring por categoria basado en feedback acumulado

### Entregable: Recomendaciones explicables con IA, feedback loop operativo

---

## Fase 6: Onboarding + Metricas (Semana 13-14)

> **Objetivo:** Optimizar conversion y retencion con datos.

### 6.1 Onboarding diferenciado
- [ ] Campo `reseller_type` en User (OA, RA, eBay flipper, local, dropshipping)
- [ ] Flujo de onboarding adaptado: presets de marketplaces, categorias sugeridas
- [ ] Tutorial interactivo: "Haz tu primer analisis en <5 min"

### 6.2 Instrumentacion de metricas
- [ ] Eventos clave:
  - Activacion: % que completa 1er analisis en <5 min
  - "Aha moment": % que crea watchlist o guarda producto
  - Conversion Free→Paid
  - Retencion D7, D30
  - Precision: falsos positivos / 100 analisis
- [ ] Integrar analytics (Mixpanel, PostHog, o custom con tablas propias)
- [ ] Dashboard interno de KPIs

### 6.3 Paginacion y mejoras de API
- [ ] Paginacion cursor-based en `/analysis/history` y `/products/search`
- [ ] Rate limiting por tier (no solo por IP)
- [ ] Endpoint de health mejorado (DB check, eBay API check)

### Entregable: Onboarding guiado, metricas instrumentadas, API production-ready

---

## Fase 7: Beta + Lanzamiento (Semana 15-16)

> **Objetivo:** 100 clientes pagadores.

### 7.1 Beta cerrada (20-50 usuarios)
- [ ] Invitar resellers del discovery (Fase 0)
- [ ] Monitorear precision de recomendaciones
- [ ] Iterar scoring basado en feedback real
- [ ] Fix bugs criticos

### 7.2 Lanzamiento publico
- [ ] Landing page con CTA a free tier
- [ ] Contenido: "flip breakdowns" diarios en TikTok/Shorts/Reels
- [ ] Comunidades: Reddit (r/flipping, r/FulfillmentByAmazon), Discords de arbitrage
- [ ] Partnerships con micro-creators de reselling (comision 10-20%)
- [ ] Oferta de arranque: "$19 primer mes" o creditos extra por feedback

### 7.3 Infraestructura de produccion
- [ ] Deploy en cloud (Railway, Fly.io, o AWS)
- [ ] CI/CD (GitHub Actions): lint, tests, deploy automatico
- [ ] Monitoreo: Sentry para errores, uptime monitoring
- [ ] Backups automaticos de DB

### Entregable: Producto en produccion con primeros clientes pagadores

---

## Resumen de prioridades criticas

```
SEMANA    PRIORIDAD                              IMPACTO
──────────────────────────────────────────────────────────
1-2       Docker + Alembic + Discovery            Fundacion
3-4       eBay conectado + datos reales            ★★★★★ (sin esto no hay producto)
5-6       Amazon SP-API + cross-market             ★★★★☆ (diferenciador clave)
7-8       Watchlist activa + alertas email          ★★★★☆ (retencion)
9-10      Stripe + limites por tier                ★★★★☆ (monetizacion)
11-12     IA + trust layer + feedback              ★★★☆☆ (diferenciacion)
13-14     Onboarding + metricas                    ★★★☆☆ (optimizacion)
15-16     Beta + lanzamiento                       ★★★★★ (go-live)
```

## Dependencias externas a resolver

| Dependencia | Bloqueante para | Accion requerida |
|---|---|---|
| Cuenta eBay Developer | Fase 1 | Registrar en developer.ebay.com, obtener App ID + Cert ID |
| Cuenta Amazon Developer | Fase 2 | Registrar en sellercentral + developer.amazonservices.com |
| Cuenta Stripe | Fase 4 | Registrar en stripe.com, configurar productos y precios |
| Cuenta SendGrid | Fase 3 | Registrar, verificar dominio de envio |
| Decision: mercado objetivo | Fase 0 | EE.UU. primero vs bilingue desde inicio |
| Decision: marketplaces MVP | Fase 0 | eBay + Amazon minimo; MercadoLibre si LatAm |
| PostgreSQL en produccion | Fase 7 | Supabase, Neon, AWS RDS, o Railway Postgres |

## Stack tecnico final (target)

```
Backend:    FastAPI + SQLAlchemy async + PostgreSQL + Redis (cache)
Workers:    Celery o ARQ (watchlist monitoring, alertas)
Auth:       JWT (migracion a Auth0 opcional)
Pagos:      Stripe
Email:      SendGrid
SMS:        Twilio (add-on)
IA:         Claude API o OpenAI
Deploy:     Docker → Railway / Fly.io / AWS
CI/CD:      GitHub Actions
Monitoring: Sentry + PostHog
```
