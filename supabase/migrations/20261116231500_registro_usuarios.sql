-- "Crear cuenta": las cuentas nuevas quedan pendientes hasta que un Fundador o Coordinador las apruebe
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS pendiente BOOLEAN NOT NULL DEFAULT FALSE;
