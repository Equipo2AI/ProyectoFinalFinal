-- Base sintética de Banamex

PRAGMA foreign_keys = OFF;

DROP VIEW  IF EXISTS vw_cliente_publico;
DROP VIEW  IF EXISTS vw_txn_firmada;
DROP VIEW  IF EXISTS vw_saldo_mensual;

DROP TABLE IF EXISTS dim_fecha;
DROP TABLE IF EXISTS dim_tipo_persona;
DROP TABLE IF EXISTS dim_genero;
DROP TABLE IF EXISTS dim_segmento;
DROP TABLE IF EXISTS dim_giro;
DROP TABLE IF EXISTS dim_direccion;
DROP TABLE IF EXISTS dim_subdireccion;
DROP TABLE IF EXISTS dim_territorio;
DROP TABLE IF EXISTS dim_sucursal;
DROP TABLE IF EXISTS dim_promotor;
DROP TABLE IF EXISTS dim_cliente;
DROP TABLE IF EXISTS dim_cliente_persona;
DROP TABLE IF EXISTS dim_dominio;
DROP TABLE IF EXISTS dim_grupo_producto;
DROP TABLE IF EXISTS dim_producto;
DROP TABLE IF EXISTS dim_txn;
DROP TABLE IF EXISTS dim_cuenta;
DROP TABLE IF EXISTS fact_saldos_captacoloca_m;
DROP TABLE IF EXISTS fact_txn_captacoloca_d;
DROP TABLE IF EXISTS raw_clientes;
DROP TABLE IF EXISTS raw_saldos;
DROP TABLE IF EXISTS raw_transacciones;
DROP TABLE IF EXISTS calidad_hallazgos_esperados;

BEGIN;

-- =============================================================================
-- 1. CALENDARIO
-- =============================================================================

CREATE TABLE dim_fecha (
    cve_fecha     INTEGER PRIMARY KEY,   -- YYYYMMDD
    fecha         TEXT    NOT NULL,
    anio          INTEGER NOT NULL,
    mes           INTEGER NOT NULL,
    dia           INTEGER NOT NULL,
    trimestre     INTEGER NOT NULL,
    nom_mes       TEXT    NOT NULL,
    anio_mes      INTEGER NOT NULL,      -- YYYYMM
    es_fin_semana INTEGER NOT NULL       -- 1 = sábado o domingo
);

INSERT INTO dim_fecha
WITH RECURSIVE calendario(fecha) AS (
    SELECT '1940-01-01'
    UNION ALL
    SELECT date(fecha, '+1 day') FROM calendario WHERE fecha < '2035-12-31'
)
SELECT
    CAST(strftime('%Y%m%d', fecha) AS INTEGER),
    fecha,
    CAST(strftime('%Y', fecha) AS INTEGER),
    CAST(strftime('%m', fecha) AS INTEGER),
    CAST(strftime('%d', fecha) AS INTEGER),
    ((CAST(strftime('%m', fecha) AS INTEGER) - 1) / 3) + 1,
    CASE CAST(strftime('%m', fecha) AS INTEGER)
        WHEN 1 THEN 'Enero' WHEN 2 THEN 'Febrero' WHEN 3 THEN 'Marzo'
        WHEN 4 THEN 'Abril' WHEN 5 THEN 'Mayo' WHEN 6 THEN 'Junio'
        WHEN 7 THEN 'Julio' WHEN 8 THEN 'Agosto' WHEN 9 THEN 'Septiembre'
        WHEN 10 THEN 'Octubre' WHEN 11 THEN 'Noviembre' ELSE 'Diciembre'
    END,
    CAST(strftime('%Y%m', fecha) AS INTEGER),
    CASE WHEN strftime('%w', fecha) IN ('0', '6') THEN 1 ELSE 0 END
FROM calendario;

-- =============================================================================
-- 2. CATÁLOGOS
-- =============================================================================

CREATE TABLE dim_tipo_persona (
    cve_tipo_persona TEXT PRIMARY KEY,
    descripcion      TEXT NOT NULL
);
INSERT INTO dim_tipo_persona VALUES ('PF', 'Persona física'), ('PM', 'Persona moral');

CREATE TABLE dim_genero (
    cve_genero  TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL
);
INSERT INTO dim_genero VALUES
    ('HOMBRE', 'Hombre'),
    ('MUJER', 'Mujer'),
    ('NO APLICA', 'No aplica (persona moral)');

CREATE TABLE dim_segmento (
    cve_segmento TEXT PRIMARY KEY,
    descripcion  TEXT NOT NULL,
    orden        INTEGER NOT NULL
);
INSERT INTO dim_segmento VALUES
    ('PREMIUM', 'Banca patrimonial', 1),
    ('PREFERENTE', 'Banca preferente', 2),
    ('MASIVO', 'Banca masiva', 3);

CREATE TABLE dim_giro (
    cve_giro    TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL
);
INSERT INTO dim_giro VALUES
    ('NO_APLICA', 'No aplica (persona física)'),
    ('GIRO_01', 'Comercio al por menor'),
    ('GIRO_02', 'Servicios financieros'),
    ('GIRO_03', 'Construcción'),
    ('GIRO_04', 'Transporte de carga'),
    ('GIRO_05', 'Tecnología de la información'),
    ('GIRO_06', 'Alimentos y bebidas'),
    ('GIRO_07', 'Servicios profesionales'),
    ('GIRO_08', 'Manufactura');

-- Jerarquía comercial: promotor -> sucursal -> territorio -> subdirección -> dirección
CREATE TABLE dim_direccion (
    cve_direccion TEXT PRIMARY KEY,
    nombre        TEXT NOT NULL
);
INSERT INTO dim_direccion
SELECT 'DIR_' || printf('%02d', n), 'Dirección ' || n FROM (WITH RECURSIVE r(n) AS (
    SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3
) SELECT n FROM r);

CREATE TABLE dim_subdireccion (
    cve_subdireccion TEXT PRIMARY KEY,
    cve_direccion    TEXT NOT NULL REFERENCES dim_direccion,
    nombre           TEXT NOT NULL
);
INSERT INTO dim_subdireccion
SELECT 'SUB_' || printf('%02d', n),
       'DIR_' || printf('%02d', ((n - 1) / 2) + 1),
       'Subdirección ' || n
FROM (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 6) SELECT n FROM r);

CREATE TABLE dim_territorio (
    cve_territorio    TEXT PRIMARY KEY,
    cve_subdireccion  TEXT NOT NULL REFERENCES dim_subdireccion,
    nombre            TEXT NOT NULL
);
INSERT INTO dim_territorio
SELECT 'TER_' || printf('%02d', n),
       'SUB_' || printf('%02d', ((n - 1) / 2) + 1),
       'Territorio ' || n
FROM (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 12) SELECT n FROM r);

CREATE TABLE dim_sucursal (
    cve_sucursal   INTEGER PRIMARY KEY,
    cve_territorio TEXT NOT NULL REFERENCES dim_territorio,
    nombre         TEXT NOT NULL
);
INSERT INTO dim_sucursal
SELECT n,
       'TER_' || printf('%02d', ((n - 1) / 3) + 1),
       'Sucursal ' || n
FROM (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 36) SELECT n FROM r);

CREATE TABLE dim_promotor (
    cve_promotor TEXT PRIMARY KEY,
    cve_sucursal INTEGER NOT NULL REFERENCES dim_sucursal,
    nombre       TEXT NOT NULL
);
INSERT INTO dim_promotor
SELECT 'PROM_' || printf('%03d', n),
       ((n - 1) / 2) + 1,
       'Promotor ' || n
FROM (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 72) SELECT n FROM r);

-- =============================================================================
-- 3. CLIENTES
-- =============================================================================

-- Maestro sin PII: 4,800 PF y 200 PM (distribución del script 001)
CREATE TABLE dim_cliente (
    num_cte         INTEGER PRIMARY KEY,
    fec_alta        TEXT NOT NULL,
    fec_nac         TEXT NOT NULL,
    cve_tipo_persona TEXT NOT NULL REFERENCES dim_tipo_persona,
    cve_genero      TEXT NOT NULL REFERENCES dim_genero,
    cve_segmento    TEXT NOT NULL REFERENCES dim_segmento,
    cve_giro        TEXT NOT NULL REFERENCES dim_giro,
    cve_promotor    TEXT NOT NULL REFERENCES dim_promotor
);

INSERT INTO dim_cliente
WITH RECURSIVE r(num_cte) AS (SELECT 1 UNION ALL SELECT num_cte + 1 FROM r WHERE num_cte < 5000)
SELECT
    num_cte,
    date('2000-01-01', '+' || ((num_cte * 97) % 7305) || ' days'),
    CASE WHEN num_cte <= 4800
         THEN date('1945-01-01', '+' || ((num_cte * 37) % 21915) || ' days')
         ELSE date('1960-01-01', '+' || ((num_cte * 53) % 23742) || ' days') END,
    CASE WHEN num_cte <= 4800 THEN 'PF' ELSE 'PM' END,
    CASE WHEN num_cte > 4800 THEN 'NO APLICA'
         WHEN num_cte <= 2496 THEN 'HOMBRE'
         ELSE 'MUJER' END,
    CASE WHEN num_cte % 100 < 5 THEN 'PREMIUM'
         WHEN num_cte % 100 < 25 THEN 'PREFERENTE'
         ELSE 'MASIVO' END,
    CASE WHEN num_cte <= 4800 THEN 'NO_APLICA'
         ELSE 'GIRO_' || printf('%02d', (num_cte % 8) + 1) END,
    'PROM_' || printf('%03d', (num_cte % 72) + 1)
FROM r;

-- =============================================================================
-- 4. DATOS PERSONALES FICTICIOS
--    Catálogos en SQL (sin Faker). Cada nombre trae ya sus iniciales, vocal y
--    consonante internas para armar RFC y CURP sin expresiones regulares.
-- =============================================================================

CREATE TABLE dim_cliente_persona (
    num_cte  INTEGER PRIMARY KEY REFERENCES dim_cliente,
    nombre   TEXT NOT NULL,
    correo   TEXT,
    telefono TEXT,
    rfc      TEXT,
    curp     TEXT
);

CREATE TEMP TABLE tmp_nombres (
    id         INTEGER NOT NULL,
    nombre     TEXT NOT NULL,
    ascii      TEXT NOT NULL,
    inicial    TEXT NOT NULL,
    vocal      TEXT NOT NULL,
    consonante TEXT NOT NULL,
    genero     TEXT NOT NULL,
    PRIMARY KEY (genero, id)
);

INSERT INTO tmp_nombres VALUES
(1, 'José', 'JOSE', 'J', 'O', 'S', 'HOMBRE'),
(2, 'Luis', 'LUIS', 'L', 'U', 'S', 'HOMBRE'),
(3, 'Carlos', 'CARLOS', 'C', 'A', 'R', 'HOMBRE'),
(4, 'Juan', 'JUAN', 'J', 'U', 'N', 'HOMBRE'),
(5, 'Miguel', 'MIGUEL', 'M', 'I', 'G', 'HOMBRE'),
(6, 'Jorge', 'JORGE', 'J', 'O', 'R', 'HOMBRE'),
(7, 'Francisco', 'FRANCISCO', 'F', 'A', 'R', 'HOMBRE'),
(8, 'Alejandro', 'ALEJANDRO', 'A', 'E', 'L', 'HOMBRE'),
(9, 'Ricardo', 'RICARDO', 'R', 'I', 'C', 'HOMBRE'),
(10, 'Fernando', 'FERNANDO', 'F', 'E', 'R', 'HOMBRE'),
(11, 'Roberto', 'ROBERTO', 'R', 'O', 'B', 'HOMBRE'),
(12, 'Daniel', 'DANIEL', 'D', 'A', 'N', 'HOMBRE'),
(13, 'Arturo', 'ARTURO', 'A', 'U', 'R', 'HOMBRE'),
(14, 'Eduardo', 'EDUARDO', 'E', 'U', 'D', 'HOMBRE'),
(15, 'Javier', 'JAVIER', 'J', 'A', 'V', 'HOMBRE'),
(16, 'Raúl', 'RAUL', 'R', 'A', 'L', 'HOMBRE'),
(17, 'Óscar', 'OSCAR', 'O', 'A', 'S', 'HOMBRE'),
(18, 'Sergio', 'SERGIO', 'S', 'E', 'R', 'HOMBRE'),
(19, 'Andrés', 'ANDRES', 'A', 'E', 'N', 'HOMBRE'),
(20, 'Manuel', 'MANUEL', 'M', 'A', 'N', 'HOMBRE'),
(21, 'Pablo', 'PABLO', 'P', 'A', 'B', 'HOMBRE'),
(22, 'Diego', 'DIEGO', 'D', 'I', 'G', 'HOMBRE'),
(23, 'Iván', 'IVAN', 'I', 'A', 'V', 'HOMBRE'),
(24, 'Héctor', 'HECTOR', 'H', 'E', 'C', 'HOMBRE'),
(25, 'Alberto', 'ALBERTO', 'A', 'E', 'L', 'HOMBRE'),
(26, 'Rubén', 'RUBEN', 'R', 'U', 'B', 'HOMBRE'),
(27, 'Mario', 'MARIO', 'M', 'A', 'R', 'HOMBRE'),
(28, 'Enrique', 'ENRIQUE', 'E', 'I', 'N', 'HOMBRE'),
(29, 'Gerardo', 'GERARDO', 'G', 'E', 'R', 'HOMBRE'),
(30, 'Rodrigo', 'RODRIGO', 'R', 'O', 'D', 'HOMBRE'),
(1, 'María', 'MARIA', 'M', 'A', 'R', 'MUJER'),
(2, 'Guadalupe', 'GUADALUPE', 'G', 'U', 'D', 'MUJER'),
(3, 'Juana', 'JUANA', 'J', 'U', 'N', 'MUJER'),
(4, 'Margarita', 'MARGARITA', 'M', 'A', 'R', 'MUJER'),
(5, 'Verónica', 'VERONICA', 'V', 'E', 'R', 'MUJER'),
(6, 'Alejandra', 'ALEJANDRA', 'A', 'E', 'L', 'MUJER'),
(7, 'Leticia', 'LETICIA', 'L', 'E', 'T', 'MUJER'),
(8, 'Rosa', 'ROSA', 'R', 'O', 'S', 'MUJER'),
(9, 'Patricia', 'PATRICIA', 'P', 'A', 'T', 'MUJER'),
(10, 'Adriana', 'ADRIANA', 'A', 'I', 'D', 'MUJER'),
(11, 'Gabriela', 'GABRIELA', 'G', 'A', 'B', 'MUJER'),
(12, 'Claudia', 'CLAUDIA', 'C', 'A', 'L', 'MUJER'),
(13, 'Fernanda', 'FERNANDA', 'F', 'E', 'R', 'MUJER'),
(14, 'Lucía', 'LUCIA', 'L', 'U', 'C', 'MUJER'),
(15, 'Carmen', 'CARMEN', 'C', 'A', 'R', 'MUJER'),
(16, 'Elena', 'ELENA', 'E', 'E', 'L', 'MUJER'),
(17, 'Sofía', 'SOFIA', 'S', 'O', 'F', 'MUJER'),
(18, 'Daniela', 'DANIELA', 'D', 'A', 'N', 'MUJER'),
(19, 'Mónica', 'MONICA', 'M', 'O', 'N', 'MUJER'),
(20, 'Silvia', 'SILVIA', 'S', 'I', 'L', 'MUJER'),
(21, 'Norma', 'NORMA', 'N', 'O', 'R', 'MUJER'),
(22, 'Alicia', 'ALICIA', 'A', 'I', 'L', 'MUJER'),
(23, 'Beatriz', 'BEATRIZ', 'B', 'E', 'T', 'MUJER'),
(24, 'Cecilia', 'CECILIA', 'C', 'E', 'C', 'MUJER'),
(25, 'Diana', 'DIANA', 'D', 'I', 'N', 'MUJER'),
(26, 'Isabel', 'ISABEL', 'I', 'A', 'S', 'MUJER'),
(27, 'Laura', 'LAURA', 'L', 'A', 'R', 'MUJER'),
(28, 'Mariana', 'MARIANA', 'M', 'A', 'R', 'MUJER'),
(29, 'Sandra', 'SANDRA', 'S', 'A', 'N', 'MUJER'),
(30, 'Teresa', 'TERESA', 'T', 'E', 'R', 'MUJER');

CREATE TEMP TABLE tmp_apellidos (
    id         INTEGER PRIMARY KEY,
    apellido   TEXT NOT NULL,
    ascii      TEXT NOT NULL,
    inicial    TEXT NOT NULL,
    vocal      TEXT NOT NULL,
    consonante TEXT NOT NULL
);

INSERT INTO tmp_apellidos VALUES
(1, 'García', 'GARCIA', 'G', 'A', 'R'),
(2, 'Hernández', 'HERNANDEZ', 'H', 'E', 'R'),
(3, 'López', 'LOPEZ', 'L', 'O', 'P'),
(4, 'Martínez', 'MARTINEZ', 'M', 'A', 'R'),
(5, 'Rodríguez', 'RODRIGUEZ', 'R', 'O', 'D'),
(6, 'Pérez', 'PEREZ', 'P', 'E', 'R'),
(7, 'González', 'GONZALEZ', 'G', 'O', 'N'),
(8, 'Sánchez', 'SANCHEZ', 'S', 'A', 'N'),
(9, 'Ramírez', 'RAMIREZ', 'R', 'A', 'M'),
(10, 'Torres', 'TORRES', 'T', 'O', 'R'),
(11, 'Flores', 'FLORES', 'F', 'O', 'L'),
(12, 'Rivera', 'RIVERA', 'R', 'I', 'V'),
(13, 'Gómez', 'GOMEZ', 'G', 'O', 'M'),
(14, 'Díaz', 'DIAZ', 'D', 'I', 'Z'),
(15, 'Cruz', 'CRUZ', 'C', 'U', 'R'),
(16, 'Morales', 'MORALES', 'M', 'O', 'R'),
(17, 'Ortiz', 'ORTIZ', 'O', 'I', 'R'),
(18, 'Gutiérrez', 'GUTIERREZ', 'G', 'U', 'T'),
(19, 'Chávez', 'CHAVEZ', 'C', 'A', 'H'),
(20, 'Ramos', 'RAMOS', 'R', 'A', 'M'),
(21, 'Ruiz', 'RUIZ', 'R', 'U', 'Z'),
(22, 'Mendoza', 'MENDOZA', 'M', 'E', 'N'),
(23, 'Vargas', 'VARGAS', 'V', 'A', 'R'),
(24, 'Castillo', 'CASTILLO', 'C', 'A', 'S'),
(25, 'Jiménez', 'JIMENEZ', 'J', 'I', 'M'),
(26, 'Rojas', 'ROJAS', 'R', 'O', 'J'),
(27, 'Vázquez', 'VAZQUEZ', 'V', 'A', 'Z'),
(28, 'Medina', 'MEDINA', 'M', 'E', 'D'),
(29, 'Suárez', 'SUAREZ', 'S', 'U', 'R'),
(30, 'Aguilar', 'AGUILAR', 'A', 'U', 'G');

-- Personas físicas: RFC = 4 letras + YYMMDD + homoclave; CURP = 18 posiciones
INSERT INTO dim_cliente_persona (num_cte, nombre, correo, telefono, rfc, curp)
WITH base AS (
    SELECT
        c.num_cte,
        c.fec_nac,
        c.cve_genero,
        n.nombre   AS pila,   n.ascii AS pila_a, n.inicial AS pila_i,
        n.vocal    AS pila_v, n.consonante AS pila_c,
        a1.apellido AS ap1,   a1.ascii AS ap1_a, a1.inicial AS ap1_i,
        a1.vocal    AS ap1_v, a1.consonante AS ap1_c,
        a2.apellido AS ap2,   a2.inicial AS ap2_i, a2.consonante AS ap2_c
    FROM dim_cliente c
    JOIN tmp_nombres n    ON n.genero = c.cve_genero AND n.id = ((c.num_cte * 7) % 30) + 1
    JOIN tmp_apellidos a1 ON a1.id = ((c.num_cte * 11) % 30) + 1
    JOIN tmp_apellidos a2 ON a2.id = ((c.num_cte * 17 + 7) % 30) + 1
    WHERE c.cve_tipo_persona = 'PF'
)
SELECT
    num_cte,
    pila || ' ' || ap1 || ' ' || ap2,
    lower(pila_a) || '.' || lower(ap1_a) || printf('%d', (num_cte % 999) + 1) || '@'
        || CASE num_cte % 4 WHEN 0 THEN 'correo.mx' WHEN 1 THEN 'mail.mx'
                            WHEN 2 THEN 'ejemplo.mx' ELSE 'banamex-demo.mx' END,
    '55' || printf('%08d', (num_cte * 7919) % 100000000),
    ap1_i || ap1_v || ap2_i || pila_i || substr(strftime('%Y%m%d', fec_nac), 3, 6)
        || substr('ABCDEFGHIJKLMNOPQRSTUVWXYZ', ((num_cte * 7) % 26) + 1, 1)
        || printf('%02d', (num_cte % 99) + 1),
    ap1_i || ap1_v || ap2_i || pila_i || substr(strftime('%Y%m%d', fec_nac), 3, 6)
        || CASE cve_genero WHEN 'HOMBRE' THEN 'H' ELSE 'M' END
        || CASE (CAST(strftime('%m', fec_nac) AS INTEGER)) % 10
               WHEN 0 THEN 'AS' WHEN 1 THEN 'BC' WHEN 2 THEN 'DF' WHEN 3 THEN 'MC'
               WHEN 4 THEN 'NL' WHEN 5 THEN 'JC' WHEN 6 THEN 'GT' WHEN 7 THEN 'PL'
               WHEN 8 THEN 'QT' ELSE 'VZ' END
        || ap1_c || ap2_c || pila_c
        || substr('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', ((num_cte * 11) % 36) + 1, 1)
        || (num_cte % 10)
FROM base;

-- Personas morales: razón social y RFC de 12 posiciones (sin CURP)
CREATE TEMP TABLE tmp_razones (
    id       INTEGER PRIMARY KEY,
    patron   TEXT NOT NULL,
    patron_a TEXT NOT NULL
);

INSERT INTO tmp_razones VALUES
(1, 'Grupo %s y Asociados',            'Grupo %s y Asociados'),
(2, 'Comercializadora %s',             'Comercializadora %s'),
(3, '%s Servicios Integrales',         '%s Servicios Integrales'),
(4, 'Distribuidora %s del Norte',      'Distribuidora %s del Norte'),
(5, 'Operadora %s',                    'Operadora %s'),
(6, 'Constructora %s',                 'Constructora %s'),
(7, 'Logística %s',                    'Logistica %s'),
(8, 'Tecnologías %s',                  'Tecnologias %s');

CREATE TEMP TABLE tmp_apellidos_moral (
    id       INTEGER PRIMARY KEY,
    apellido TEXT NOT NULL
);

INSERT INTO tmp_apellidos_moral VALUES
(1, 'Delta'), (2, 'Nova'), (3, 'Azteca'), (4, 'Monterrey'),
(5, 'Bajío'), (6, 'Cobalto'), (7, 'Águila'), (8, 'Sur');

INSERT INTO dim_cliente_persona (num_cte, nombre, correo, telefono, rfc, curp)
WITH base AS (
    SELECT
        c.num_cte,
        c.fec_alta,
        replace(r.patron,   '%s', a.apellido) || ' S.A. de C.V.' AS razon,
        replace(r.patron_a, '%s', a.apellido) || ' S.A. de C.V.' AS razon_a
    FROM dim_cliente c
    JOIN tmp_razones r          ON r.id = (c.num_cte % 8) + 1
    JOIN tmp_apellidos_moral a  ON a.id = (c.num_cte % 8) + 1
    WHERE c.cve_tipo_persona = 'PM'
)
SELECT
    num_cte,
    razon,
    lower(substr(razon_a, 1, 3)) || printf('%d', (num_cte % 999) + 1) || '@empresa.mx',
    '55' || printf('%08d', (num_cte * 7919) % 100000000),
    upper(substr(razon_a, 1, 3)) || substr(strftime('%Y%m%d', fec_alta), 3, 6)
        || substr('ABCDEFGHIJKLMNOPQRSTUVWXYZ', ((num_cte * 7) % 26) + 1, 1)
        || printf('%02d', (num_cte % 99) + 1),
    NULL
FROM base;

DROP TABLE tmp_nombres;
DROP TABLE tmp_apellidos;
DROP TABLE tmp_razones;
DROP TABLE tmp_apellidos_moral;

-- =============================================================================
-- 5. PRODUCTOS Y TIPOS DE MOVIMIENTO
-- =============================================================================

CREATE TABLE dim_dominio (
    cve_dominio TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL
);
INSERT INTO dim_dominio VALUES
    ('DEPOSITS', 'Captación'),
    ('LENDING', 'Colocación'),
    ('TDC', 'Tarjeta de crédito');

CREATE TABLE dim_grupo_producto (
    cve_grupo   TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL
);
INSERT INTO dim_grupo_producto VALUES
    ('CAPTACION', 'Captación'),
    ('COLOCACION', 'Colocación');

CREATE TABLE dim_producto (
    cve_producto TEXT PRIMARY KEY,
    cve_dominio  TEXT NOT NULL REFERENCES dim_dominio,
    cve_grupo    TEXT NOT NULL REFERENCES dim_grupo_producto,
    descripcion  TEXT NOT NULL,
    activo       INTEGER NOT NULL
);
INSERT INTO dim_producto VALUES
    ('CHEQUES',    'DEPOSITS', 'CAPTACION',  'Cuenta de Cheques', 1),
    ('AHORRO',     'DEPOSITS', 'CAPTACION',  'Cuenta de Ahorro', 1),
    ('C_PERSONAL', 'LENDING',  'COLOCACION', 'Crédito Personal', 1),
    ('TDC',        'TDC',      'COLOCACION', 'Tarjeta de Crédito', 1);

-- signo: +1 aumenta el saldo (o la deuda en colocación), -1 lo reduce
CREATE TABLE dim_txn (
    cve_producto TEXT NOT NULL,
    cve_txn      INTEGER NOT NULL,
    desc_txn     TEXT NOT NULL,
    signo        INTEGER NOT NULL,
    PRIMARY KEY (cve_producto, cve_txn)
);
INSERT INTO dim_txn VALUES
    ('CHEQUES',    100, 'DEPOSITO', 1),
    ('CHEQUES',    200, 'RETIRO', -1),
    ('AHORRO',     100, 'DEPOSITO', 1),
    ('AHORRO',     200, 'RETIRO', -1),
    ('C_PERSONAL', 300, 'PAGOS', -1),
    ('C_PERSONAL', 450, 'DISPOSICIONES', 1),
    ('TDC',        300, 'PAGOS', -1),
    ('TDC',        400, 'COMPRAS', 1),
    ('TDC',        450, 'DISPOSICIONES', 1);

-- =============================================================================
-- 6. CUENTAS Y SALDOS
-- =============================================================================

CREATE TABLE dim_cuenta (
    num_cta      INTEGER PRIMARY KEY,
    num_cte      INTEGER NOT NULL REFERENCES dim_cliente,
    cve_producto TEXT NOT NULL REFERENCES dim_producto,
    fec_alta     TEXT NOT NULL,
    cve_mes_alta INTEGER NOT NULL
);

INSERT INTO dim_cuenta
SELECT
    p.inicio + (ROW_NUMBER() OVER (PARTITION BY p.cve_producto ORDER BY c.num_cte) - 1),
    c.num_cte,
    p.cve_producto,
    '2026-01-01',
    202601
FROM dim_cliente c
CROSS JOIN (
    SELECT 'CHEQUES'    AS cve_producto, 100000 AS inicio UNION ALL
    SELECT 'AHORRO',     200000 UNION ALL
    SELECT 'TDC',        300000 UNION ALL
    SELECT 'C_PERSONAL', 400000
) p;

CREATE TABLE fact_saldos_captacoloca_m (
    num_cta               INTEGER NOT NULL REFERENCES dim_cuenta,
    cve_fecha_informacion INTEGER NOT NULL,
    saldo_apertura        REAL NOT NULL,
    saldo_mes             REAL NOT NULL,
    PRIMARY KEY (num_cta, cve_fecha_informacion)
);

-- Saldos de apertura de enero por rango de producto (script 002).
-- u = ruido determinístico en [0,1): mismo resultado en cada corrida.
-- u = ruido determinístico en [0,1): mismo resultado en cada corrida.
CREATE TEMP TABLE tmp_enero AS
SELECT
    c.num_cta,
    ROUND(CASE c.cve_producto
        WHEN 'CHEQUES'    THEN 8000  + (((c.num_cta * 1103515245 + 12345) % 2147483648) / 2147483648.0) * 4000
        WHEN 'AHORRO'     THEN 5     + (((c.num_cta * 1103515245 + 12345) % 2147483648) / 2147483648.0) * 9995
        WHEN 'TDC'        THEN 10000 + (((c.num_cta * 1103515245 + 12345) % 2147483648) / 2147483648.0) * 110000
        WHEN 'C_PERSONAL' THEN 50000 + (((c.num_cta * 1103515245 + 12345) % 2147483648) / 2147483648.0) * 35000
    END, 2) AS saldo
FROM dim_cuenta c
ORDER BY c.num_cta;

INSERT INTO fact_saldos_captacoloca_m
SELECT num_cta, 20260131, saldo, saldo FROM tmp_enero;

-- Movimientos y saldos de cierre feb-jun (script 003).
-- La recursión arrastra el saldo de cada cuenta mes a mes.
CREATE TEMP TABLE tmp_flujo AS
WITH RECURSIVE estado (mes, num_cta, cve_producto, cve_grupo, saldo_anterior, saldo) AS (
    SELECT
        202602,
        s.num_cta,
        c.cve_producto,
        p.cve_grupo,
        s.saldo_mes,
        ROUND(s.saldo_mes * (1 + CASE WHEN p.cve_grupo = 'CAPTACION' THEN 0.020 ELSE 0.030 END), 2)
    FROM fact_saldos_captacoloca_m s
    JOIN dim_cuenta c     ON c.num_cta = s.num_cta
    JOIN dim_producto p   ON p.cve_producto = c.cve_producto
    WHERE s.cve_fecha_informacion = 20260131

    UNION ALL

    SELECT
        e.mes + 1,
        e.num_cta,
        e.cve_producto,
        e.cve_grupo,
        e.saldo,
        ROUND(e.saldo * (1 + CASE WHEN e.cve_grupo = 'CAPTACION' THEN 0.010 ELSE 0.022 END), 2)
    FROM estado e
    WHERE e.mes < 202606
)
SELECT
    mes, num_cta, cve_producto, cve_grupo, saldo_anterior, saldo,
    ROUND(saldo - saldo_anterior, 2) AS incremento,
    CAST(strftime('%Y%m%d',
        date(printf('%04d-%02d-01', mes / 100, mes % 100), '+1 month', '-1 day')) AS INTEGER) AS corte,
    date(printf('%04d-%02d-15', mes / 100, mes % 100)) AS fec_txn
FROM estado;

CREATE TABLE fact_txn_captacoloca_d (
    num_cta               INTEGER NOT NULL,
    cve_producto          TEXT NOT NULL,
    cve_txn               INTEGER NOT NULL,
    fec_txn               TEXT NOT NULL,
    cve_fecha_informacion INTEGER NOT NULL,
    saldo_txn             REAL NOT NULL,
    PRIMARY KEY (num_cta, cve_producto, cve_txn, cve_fecha_informacion)
);

-- Movimiento 1: entrada de recursos (depósito / disposición / compra)
INSERT INTO fact_txn_captacoloca_d
SELECT
    num_cta, cve_producto,
    CASE WHEN cve_grupo = 'CAPTACION' THEN 100
         WHEN cve_producto = 'C_PERSONAL' THEN 450
         ELSE 400 END,
    fec_txn, corte,
    ROUND(CASE
        WHEN cve_grupo = 'CAPTACION' THEN ROUND(saldo_anterior * 0.05, 2) + incremento
        WHEN cve_producto = 'C_PERSONAL' THEN ROUND(saldo_anterior * 0.05, 2) + incremento
        ELSE saldo_anterior * 0.02
    END, 2)
FROM tmp_flujo;

-- Movimiento 2: salida de recursos (retiro / pago)
INSERT INTO fact_txn_captacoloca_d
SELECT
    num_cta, cve_producto,
    CASE WHEN cve_grupo = 'CAPTACION' THEN 200 ELSE 300 END,
    fec_txn, corte,
    ROUND(CASE
        WHEN cve_grupo = 'CAPTACION' THEN ROUND(saldo_anterior * 0.05, 2)
        WHEN cve_producto = 'C_PERSONAL' THEN ROUND(saldo_anterior * 0.05, 2)
        ELSE ROUND(saldo_anterior * 0.02, 2) * 2 - incremento
    END, 2)
FROM tmp_flujo;

-- Movimiento 3: disposiciones, solo tarjeta de crédito
INSERT INTO fact_txn_captacoloca_d
SELECT num_cta, cve_producto, 450, fec_txn, corte, ROUND(saldo_anterior * 0.02, 2)
FROM tmp_flujo
WHERE cve_producto = 'TDC';

INSERT INTO fact_saldos_captacoloca_m
SELECT num_cta, corte, saldo_anterior, saldo FROM tmp_flujo;

DROP TABLE tmp_flujo;
DROP TABLE tmp_enero;

-- =============================================================================
-- 7. VISTAS DE CONSUMO
-- =============================================================================

CREATE VIEW vw_cliente_publico AS
SELECT c.num_cte, c.fec_alta, c.fec_nac,
       tp.descripcion AS tipo_persona, g.descripcion AS genero,
       s.cve_segmento, s.descripcion AS segmento, gi.descripcion AS giro,
       pr.cve_promotor, pr.nombre AS promotor,
       su.cve_sucursal, su.nombre AS sucursal,
       te.cve_territorio, te.nombre AS territorio,
       sd.nombre AS subdireccion, di.nombre AS direccion
FROM dim_cliente c
JOIN dim_tipo_persona tp ON tp.cve_tipo_persona = c.cve_tipo_persona
JOIN dim_genero g        ON g.cve_genero = c.cve_genero
JOIN dim_segmento s      ON s.cve_segmento = c.cve_segmento
JOIN dim_giro gi         ON gi.cve_giro = c.cve_giro
JOIN dim_promotor pr     ON pr.cve_promotor = c.cve_promotor
JOIN dim_sucursal su     ON su.cve_sucursal = pr.cve_sucursal
JOIN dim_territorio te   ON te.cve_territorio = su.cve_territorio
JOIN dim_subdireccion sd ON sd.cve_subdireccion = te.cve_subdireccion
JOIN dim_direccion di    ON di.cve_direccion = sd.cve_direccion;

CREATE VIEW vw_txn_firmada AS
SELECT t.num_cta, c.num_cte, t.cve_producto, p.cve_dominio, p.cve_grupo,
       t.cve_txn, tx.desc_txn, tx.signo, t.fec_txn, t.cve_fecha_informacion,
       t.saldo_txn,
       ROUND(t.saldo_txn * tx.signo, 2) AS importe_firmado
FROM fact_txn_captacoloca_d t
JOIN dim_cuenta c   ON c.num_cta = t.num_cta
JOIN dim_producto p ON p.cve_producto = t.cve_producto
JOIN dim_txn tx     ON tx.cve_producto = t.cve_producto AND tx.cve_txn = t.cve_txn;

CREATE VIEW vw_saldo_mensual AS
SELECT s.num_cta, c.num_cte, c.cve_producto, p.cve_dominio, p.cve_grupo,
       s.cve_fecha_informacion, f.fecha AS fec_corte, f.anio_mes,
       s.saldo_apertura, s.saldo_mes,
       LAG(s.saldo_mes) OVER (PARTITION BY s.num_cta ORDER BY s.cve_fecha_informacion) AS saldo_mes_anterior
FROM fact_saldos_captacoloca_m s
JOIN dim_cuenta c   ON c.num_cta = s.num_cta
JOIN dim_producto p ON p.cve_producto = c.cve_producto
JOIN dim_fecha f    ON f.cve_fecha = s.cve_fecha_informacion;

-- =============================================================================
-- 8. CAPA SUCIA (defectos deliberados para el Data Steward)
-- =============================================================================

CREATE TABLE raw_clientes AS
SELECT c.num_cte, p.nombre, p.correo, p.telefono, p.rfc, p.curp,
       c.fec_alta, c.fec_nac, c.cve_tipo_persona AS tipo_persona,
       c.cve_genero AS genero, c.cve_segmento, s.descripcion AS segmento,
       g.descripcion AS giro, c.cve_promotor
FROM dim_cliente c
JOIN dim_cliente_persona p ON p.num_cte = c.num_cte
JOIN dim_segmento s        ON s.cve_segmento = c.cve_segmento
JOIN dim_giro g            ON g.cve_giro = c.cve_giro;

UPDATE raw_clientes SET cve_segmento = NULL, segmento = NULL WHERE num_cte BETWEEN 1 AND 40;
UPDATE raw_clientes SET fec_nac = '2031-06-01' WHERE num_cte BETWEEN 100 AND 107;
UPDATE raw_clientes SET correo = 'correo.invalido' WHERE num_cte BETWEEN 200 AND 211;
UPDATE raw_clientes SET curp = 'XXXX' WHERE num_cte BETWEEN 300 AND 304;

CREATE TEMP TABLE tmp_dup AS SELECT * FROM raw_clientes WHERE num_cte BETWEEN 1000 AND 1014;
INSERT INTO raw_clientes SELECT * FROM tmp_dup;
DROP TABLE tmp_dup;

CREATE TABLE raw_saldos AS
SELECT c.num_cta, c.cve_producto, s.cve_fecha_informacion, s.saldo_mes
FROM dim_cuenta c
JOIN fact_saldos_captacoloca_m s ON s.num_cta = c.num_cta
WHERE s.cve_fecha_informacion = 20260131;

UPDATE raw_saldos SET saldo_mes = -saldo_mes WHERE cve_producto = 'AHORRO' AND num_cta BETWEEN 200000 AND 200024;
UPDATE raw_saldos SET saldo_mes = NULL       WHERE cve_producto = 'AHORRO' AND num_cta BETWEEN 200025 AND 200054;

CREATE TEMP TABLE tmp_dup AS SELECT * FROM raw_saldos WHERE cve_producto = 'CHEQUES' AND num_cta BETWEEN 100000 AND 100009;
INSERT INTO raw_saldos SELECT * FROM tmp_dup;
DROP TABLE tmp_dup;

INSERT INTO raw_saldos VALUES
    (999001, 'CUENTA_EJE', 20260131, 1500.00),
    (999002, 'CUENTA_EJE', 20260131, 2300.00),
    (999003, 'CUENTA_EJE', 20260131, 980.00);

CREATE TABLE raw_transacciones AS
SELECT num_cta, cve_producto, cve_txn, fec_txn, cve_fecha_informacion, saldo_txn
FROM fact_txn_captacoloca_d
WHERE cve_fecha_informacion = 20260228;

UPDATE raw_transacciones SET saldo_txn = 0             WHERE num_cta BETWEEN 100000 AND 100009;
UPDATE raw_transacciones SET saldo_txn = -saldo_txn    WHERE num_cta BETWEEN 100010 AND 100019;
UPDATE raw_transacciones SET fec_txn = '2030-01-15'    WHERE num_cta BETWEEN 200000 AND 200005;
UPDATE raw_transacciones SET cve_txn = 999             WHERE num_cta BETWEEN 300000 AND 300004;

CREATE TABLE calidad_hallazgos_esperados (
    id              INTEGER PRIMARY KEY,
    tabla           TEXT NOT NULL,
    columna         TEXT NOT NULL,
    tipo_defecto    TEXT NOT NULL,
    filas_afectadas INTEGER NOT NULL,
    descripcion     TEXT NOT NULL
);
INSERT INTO calidad_hallazgos_esperados VALUES
    ( 1, 'raw_clientes',      'segmento',   'valor_nulo',        40, 'Clientes sin segmento asignado'),
    ( 2, 'raw_clientes',      'fec_nac',    'fecha_futura',       8, 'Fecha de nacimiento posterior a 2026'),
    ( 3, 'raw_clientes',      'correo',     'formato_invalido',  12, 'Correo sin arroba'),
    ( 4, 'raw_clientes',      'curp',       'formato_invalido',   5, 'CURP con longitud incorrecta'),
    ( 5, 'raw_clientes',      '*',          'fila_duplicada',    15, 'Filas sobrantes duplicadas exactas'),
    ( 6, 'raw_saldos',        'saldo_mes',  'valor_nulo',        30, 'Saldos sin importe'),
    ( 7, 'raw_saldos',        'saldo_mes',  'valor_negativo',    25, 'Saldo negativo en cuenta de ahorro'),
    ( 8, 'raw_saldos',        'num_cta,cve_fecha_informacion', 'fila_duplicada', 10, 'Filas sobrantes duplicadas por llave'),
    ( 9, 'raw_saldos',        'cve_producto', 'valor_no_catalogo', 3, 'Producto CUENTA_EJE inexistente'),
    (10, 'raw_transacciones', 'saldo_txn',  'valor_no_positivo', 40, 'Importes en cero o negativos'),
    (11, 'raw_transacciones', 'fec_txn',    'fecha_futura',      12, 'Movimientos con fecha 2030'),
    (12, 'raw_transacciones', 'cve_txn',    'valor_no_catalogo', 15, 'Tipo de transacción 999 inexistente');

COMMIT;

-- =============================================================================
-- 9. VALIDACIONES
--    Portadas de las consultas de verificación de los scripts 001, 002 y 003.
--    Niveles: ERROR (rompe el modelo), WARNING (defecto esperado en raw),
--             INFO (informativo).
-- =============================================================================

CREATE TEMP TABLE tmp_checks (
    id          INTEGER PRIMARY KEY,
    nivel       TEXT NOT NULL,
    fuente      TEXT NOT NULL,
    chequeo     TEXT NOT NULL,
    esperado    INTEGER NOT NULL,
    obtenido    INTEGER NOT NULL,
    descripcion TEXT NOT NULL
);

INSERT INTO tmp_checks VALUES
( 1, 'ERROR',   '001', 'Total de clientes', 5000,
     (SELECT COUNT(*) FROM dim_cliente), 'El script 001 genera 5,000 clientes.'),
( 2, 'ERROR',   '001', 'Clientes persona física', 4800,
     (SELECT COUNT(*) FROM dim_cliente WHERE cve_tipo_persona = 'PF'), 'Distribución 96% PF.'),
( 3, 'ERROR',   '001', 'Clientes persona moral', 200,
     (SELECT COUNT(*) FROM dim_cliente WHERE cve_tipo_persona = 'PM'), 'Distribución 4% PM.'),
( 4, 'ERROR',   '001', 'Hombres PF', 2496,
     (SELECT COUNT(*) FROM dim_cliente WHERE cve_genero = 'HOMBRE'), '52% de los PF.'),
( 5, 'ERROR',   '001', 'Mujeres PF', 2304,
     (SELECT COUNT(*) FROM dim_cliente WHERE cve_genero = 'MUJER'), '48% de los PF.'),
( 6, 'ERROR',   '001', 'Fechas de alta o nacimiento nulas', 0,
     (SELECT COUNT(*) FROM dim_cliente WHERE fec_alta IS NULL OR fec_nac IS NULL), 'Todas las fechas completas.'),
( 7, 'ERROR',   '001', 'Clientes PF menores de edad', 0,
     (SELECT COUNT(*) FROM dim_cliente WHERE cve_tipo_persona = 'PF' AND fec_nac > '2008-07-15'),
     'Mayores de 18 años al 2026-07-15.'),
( 8, 'INFO',    '001', 'Códigos cve_txn compartidos entre productos', 4,
     (SELECT COUNT(*) FROM (SELECT cve_txn FROM dim_txn GROUP BY cve_txn HAVING COUNT(*) > 1)),
     'Los JOIN a dim_txn deben usar cve_producto + cve_txn.'),
( 9, 'ERROR',   '002', 'Cuentas distintas', 20000,
     (SELECT COUNT(DISTINCT num_cta) FROM dim_cuenta), '5,000 clientes x 4 productos.'),
(10, 'ERROR',   '002', 'Registros de enero por producto (mínimo)', 5000,
     (SELECT MIN(n) FROM (SELECT COUNT(*) AS n FROM fact_saldos_captacoloca_m s
          JOIN dim_cuenta c ON c.num_cta = s.num_cta
          WHERE s.cve_fecha_informacion = 20260131 GROUP BY c.cve_producto)),
     'Cada producto debe tener 5,000 registros de enero.'),
(11, 'ERROR',   '002', 'Registros de enero por producto (máximo)', 5000,
     (SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM fact_saldos_captacoloca_m s
          JOIN dim_cuenta c ON c.num_cta = s.num_cta
          WHERE s.cve_fecha_informacion = 20260131 GROUP BY c.cve_producto)),
     'Ningún producto debe exceder 5,000 registros de enero.'),
(12, 'ERROR',   '002', 'Saldos de apertura fuera de rango', 0,
     (SELECT COUNT(*) FROM fact_saldos_captacoloca_m s
          JOIN dim_cuenta c ON c.num_cta = s.num_cta
          WHERE s.cve_fecha_informacion = 20260131 AND NOT (
              (c.cve_producto = 'CHEQUES'    AND s.saldo_apertura BETWEEN 8000  AND 12000)
           OR (c.cve_producto = 'AHORRO'     AND s.saldo_apertura BETWEEN 5     AND 10000)
           OR (c.cve_producto = 'TDC'        AND s.saldo_apertura BETWEEN 10000 AND 120000)
           OR (c.cve_producto = 'C_PERSONAL' AND s.saldo_apertura BETWEEN 50000 AND 85000)
          )), 'Rangos autorizados por producto.'),
(13, 'ERROR',   '002', 'Clientes sin exactamente 4 cuentas', 0,
     (SELECT COUNT(*) FROM (SELECT num_cte FROM dim_cuenta GROUP BY num_cte HAVING COUNT(*) <> 4)),
     'Una cuenta por producto para cada cliente.'),
(14, 'ERROR',   '002', 'Saldos de enero nulos', 0,
     (SELECT COUNT(*) FROM fact_saldos_captacoloca_m
          WHERE cve_fecha_informacion = 20260131 AND (saldo_apertura IS NULL OR saldo_mes IS NULL)),
     'Sin importes faltantes.'),
(15, 'ERROR',   '003', 'Movimientos por mes (mínimo)', 45000,
     (SELECT MIN(n) FROM (SELECT COUNT(*) AS n FROM fact_txn_captacoloca_d GROUP BY cve_fecha_informacion)),
     '45,000 movimientos por mes: 20,000 + 20,000 + 5,000.'),
(16, 'ERROR',   '003', 'Movimientos por mes (máximo)', 45000,
     (SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM fact_txn_captacoloca_d GROUP BY cve_fecha_informacion)),
     'Ningún mes debe exceder 45,000 movimientos.'),
(17, 'ERROR',   '003', 'Saldos de cierre por mes', 20000,
     (SELECT MIN(n) FROM (SELECT COUNT(*) AS n FROM fact_saldos_captacoloca_m GROUP BY cve_fecha_informacion)),
     '20,000 saldos en cada uno de los 6 cortes.'),
(18, 'ERROR',   '003', 'Importes de transacción nulos o no positivos', 0,
     (SELECT COUNT(*) FROM fact_txn_captacoloca_d WHERE saldo_txn IS NULL OR saldo_txn <= 0),
     'saldo_txn siempre positivo; el signo vive en dim_txn.'),
(19, 'ERROR',   '003', 'Cuentas con crecimiento distinto al objetivo', 0,
     (SELECT COUNT(*) FROM (
         SELECT saldo_mes, ROUND(saldo_mes_anterior * (1 + CASE
             WHEN anio_mes = 202602 AND cve_grupo = 'CAPTACION' THEN 0.020
             WHEN anio_mes = 202602 THEN 0.030
             WHEN cve_grupo = 'CAPTACION' THEN 0.010
             ELSE 0.022 END), 2) AS esperado
         FROM vw_saldo_mensual WHERE saldo_mes_anterior IS NOT NULL) x
      WHERE ABS(saldo_mes - esperado) > 0.005),
     'Feb: +2.0% captación / +3.0% colocación. Mar-jun: +1.0% / +2.2%.'),
(20, 'ERROR',   '003', 'Cuentas que no reconcilian saldo con movimientos', 0,
     (SELECT COUNT(*) FROM (
         SELECT s.num_cta, s.cve_fecha_informacion
         FROM fact_saldos_captacoloca_m s
         LEFT JOIN (SELECT num_cta, cve_fecha_informacion, SUM(importe_firmado) AS neto
                    FROM vw_txn_firmada GROUP BY num_cta, cve_fecha_informacion) t
                ON t.num_cta = s.num_cta AND t.cve_fecha_informacion = s.cve_fecha_informacion
         WHERE s.cve_fecha_informacion > 20260131
           AND ABS(s.saldo_mes - (s.saldo_apertura + COALESCE(t.neto, 0))) > 0.01) x),
     'saldo_apertura + suma de importes firmados = saldo_mes.'),
(21, 'ERROR',   'PII', 'Personas registradas', 5000,
     (SELECT COUNT(*) FROM dim_cliente_persona), 'Un registro de PII por cliente.'),
(22, 'ERROR',   'PII', 'RFC con formato inválido', 0,
     (SELECT COUNT(*) FROM dim_cliente_persona
      WHERE rfc IS NULL OR NOT (
          (length(rfc) = 13 AND rfc GLOB '[A-Z][A-Z][A-Z][A-Z][0-9][0-9][0-9][0-9][0-9][0-9][A-Z0-9][A-Z0-9][A-Z0-9]')
       OR (length(rfc) = 12 AND rfc GLOB '[A-Z][A-Z][A-Z][0-9][0-9][0-9][0-9][0-9][0-9][A-Z0-9][A-Z0-9][A-Z0-9]')
      )), 'Formato de RFC.'),
(23, 'ERROR',   'PII', 'CURP de personas físicas con formato inválido', 0,
     (SELECT COUNT(*) FROM dim_cliente_persona p
      JOIN dim_cliente c ON c.num_cte = p.num_cte
      WHERE c.cve_tipo_persona = 'PF' AND (p.curp IS NULL OR NOT (
          length(p.curp) = 18 AND p.curp GLOB
          '[A-Z][A-Z][A-Z][A-Z][0-9][0-9][0-9][0-9][0-9][0-9][HM][A-Z][A-Z][A-Z][A-Z][A-Z][A-Z0-9][0-9]'
      ))), 'Formato de CURP.'),
(24, 'ERROR',   'PII', 'Correos con formato inválido', 0,
     (SELECT COUNT(*) FROM dim_cliente_persona WHERE correo NOT LIKE '%_@_%._%'), 'Formato de correo.'),
(25, 'WARNING', 'raw_*', 'Clientes sin segmento', 40,
     (SELECT COUNT(*) FROM raw_clientes WHERE segmento IS NULL), 'Defecto inyectado: valor nulo.'),
(26, 'WARNING', 'raw_*', 'Fechas de nacimiento futuras', 8,
     (SELECT COUNT(*) FROM raw_clientes WHERE fec_nac > '2026-07-15'), 'Defecto inyectado: fecha imposible.'),
(27, 'WARNING', 'raw_*', 'Correos con formato inválido', 12,
     (SELECT COUNT(*) FROM raw_clientes WHERE correo NOT LIKE '%@%'), 'Defecto inyectado: correo sin arroba.'),
(28, 'WARNING', 'raw_*', 'CURP con formato inválido', 5,
     (SELECT COUNT(*) FROM raw_clientes WHERE curp IS NOT NULL AND length(curp) <> 18),
     'Defecto inyectado: longitud incorrecta.'),
(29, 'WARNING', 'raw_*', 'Filas duplicadas exactas en clientes', 15,
     (SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw_clientes)) FROM raw_clientes),
     'Filas sobrantes duplicadas.'),
(30, 'WARNING', 'raw_*', 'Saldos nulos', 30,
     (SELECT COUNT(*) FROM raw_saldos WHERE saldo_mes IS NULL), 'Defecto inyectado: valor nulo.'),
(31, 'WARNING', 'raw_*', 'Saldos negativos en ahorro', 25,
     (SELECT COUNT(*) FROM raw_saldos WHERE saldo_mes < 0), 'Defecto inyectado: importe negativo.'),
(32, 'WARNING', 'raw_*', 'Filas duplicadas por llave en saldos', 10,
     (SELECT COUNT(*) - (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw_saldos)) FROM raw_saldos),
     'Misma cuenta y fecha repetidas.'),
(33, 'WARNING', 'raw_*', 'Productos inexistentes en saldos', 3,
     (SELECT COUNT(*) FROM raw_saldos WHERE cve_producto = 'CUENTA_EJE'), 'Valor fuera del catálogo.'),
(34, 'WARNING', 'raw_*', 'Movimientos con importe no positivo', 40,
     (SELECT COUNT(*) FROM raw_transacciones WHERE saldo_txn <= 0), 'Defecto inyectado: importes en cero o negativos.'),
(35, 'WARNING', 'raw_*', 'Movimientos con fecha futura', 12,
     (SELECT COUNT(*) FROM raw_transacciones WHERE fec_txn > '2026-07-15'), 'Defecto inyectado: fecha 2030.'),
(36, 'WARNING', 'raw_*', 'Movimientos con tipo inexistente', 15,
     (SELECT COUNT(*) FROM raw_transacciones WHERE cve_txn NOT IN (SELECT cve_txn FROM dim_txn)),
     'Valor fuera del catálogo (cve_txn = 999).');

CREATE TEMP VIEW tmp_resultado AS
SELECT id, nivel, fuente, chequeo, esperado, obtenido,
       CASE WHEN obtenido = esperado THEN 'OK' ELSE 'FALLA' END AS estado,
       descripcion
FROM tmp_checks;

-- Solo se listan los checks que fallan (deben ser cero).
SELECT nivel, fuente, chequeo, esperado, obtenido
FROM tmp_resultado
WHERE estado = 'FALLA'
ORDER BY id;

SELECT
    COUNT(*) AS checks,
    COUNT(*) FILTER (WHERE nivel = 'WARNING') AS warnings,
    COUNT(*) FILTER (WHERE nivel = 'INFO')    AS informativos,
    COUNT(*) FILTER (WHERE estado = 'FALLA')  AS fallas
FROM tmp_resultado;
