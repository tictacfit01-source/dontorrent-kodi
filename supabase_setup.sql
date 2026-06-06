-- =============================================================================
-- MejorWolf / DonTorrent — Supabase Central Config
-- Ejecutar en: Supabase Dashboard > SQL Editor > New Query
-- =============================================================================

-- 1) Tabla de configuracion centralizada
CREATE TABLE IF NOT EXISTS mw_config (
  key         TEXT        PRIMARY KEY,
  value       JSONB       NOT NULL DEFAULT '{}',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger para auto-actualizar updated_at
CREATE OR REPLACE FUNCTION mw_config_updated()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mw_config_updated ON mw_config;
CREATE TRIGGER trg_mw_config_updated
  BEFORE UPDATE ON mw_config
  FOR EACH ROW EXECUTE FUNCTION mw_config_updated();

-- 2) Row Level Security: lectura publica, escritura solo autenticado
ALTER TABLE mw_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_read"  ON mw_config;
DROP POLICY IF EXISTS "auth_write" ON mw_config;

CREATE POLICY "anon_read"  ON mw_config FOR SELECT USING (true);
CREATE POLICY "auth_write" ON mw_config FOR ALL    USING (auth.role() = 'authenticated');

-- 3) Datos iniciales
INSERT INTO mw_config (key, value) VALUES
  ('dontorrent', jsonb_build_object(
      'domain',    'dontorrent.rocks',
      'fallbacks', '["dontorrent.racing","dontorrent.quest"]'::jsonb,
      'telegram',  'DonTorrent'
  )),
  ('wolfmax', jsonb_build_object(
      'domain',    'wolfmax4k.com',
      'fallbacks', '["wolfmax4k.net"]'::jsonb,
      'telegram',  'WolfMax4k'
  )),
  ('seriesly', jsonb_build_object(
      'enabled',  true,
      'base_url', 'https://series.ly',
      'notes',    'Auth via session cookie. User must provide seriesly_session in addon settings.'
  )),
  ('relay', jsonb_build_object(
      'url', 'https://mw-render-relay-us.onrender.com'
  )),
  ('addon_mejorwolf', jsonb_build_object(
      'version',   '1.0.0',
      'changelog', 'Supabase sync + series.ly integration'
  )),
  ('addon_dontorrent', jsonb_build_object(
      'version',   '0.7.0',
      'changelog', 'Supabase sync + domain auto-update'
  ))
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- 4) Tabla opcional de cache de busquedas (reduce scraping repetitivo)
CREATE TABLE IF NOT EXISTS mw_search_cache (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  source      TEXT        NOT NULL,   -- 'dontorrent', 'wolfmax', 'seriesly'
  query       TEXT        NOT NULL,
  results     JSONB       NOT NULL DEFAULT '[]',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mw_search_source_query
  ON mw_search_cache (source, query);

ALTER TABLE mw_search_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_read_cache"  ON mw_search_cache;
DROP POLICY IF EXISTS "anon_write_cache" ON mw_search_cache;
CREATE POLICY "anon_read_cache"  ON mw_search_cache FOR SELECT USING (true);
CREATE POLICY "anon_write_cache" ON mw_search_cache FOR INSERT WITH CHECK (true);

-- Limpieza automatica: borrar cache > 2 horas (ejecutar como pg_cron o manual)
-- SELECT cron.schedule('clean_search_cache', '0 * * * *',
--   $$DELETE FROM mw_search_cache WHERE created_at < now() - interval '2 hours'$$);

-- =============================================================================
-- Listo. Ahora copia tu Project URL y anon key desde:
--   Supabase Dashboard > Settings > API
-- y ponlos en los ajustes de los addons de Kodi.
-- =============================================================================
