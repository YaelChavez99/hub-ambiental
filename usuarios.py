"""
Módulo: usuarios.py
Sistema de autenticación multi-usuario con trazabilidad completa.

IMPORTANTE — Modelo de datos:
  Los proyectos NO se aíslan por usuario. Cualquier usuario autenticado
  puede ver y trabajar sobre cualquier proyecto. Lo que se aísla es la
  IDENTIDAD de quién hizo cada acción, para auditoría completa.

Responsabilidades:
  1. migrar_bd_usuarios()    — crea tabla usuarios (idempotente)
  2. crear_usuario_inicial() — siembra el admin desde secrets.toml
  3. autenticar()            — valida usuario/contraseña con hash bcrypt
  4. crear_usuario()         — alta de nuevos usuarios (solo admin)
  5. listar_usuarios()       — para el panel de administración
  6. render_login()          — pantalla de login multi-usuario
  7. render_panel_usuarios() — administración de usuarios (solo admin)

Compatible con: Python 3.10+, psycopg2>=2.9, streamlit>=1.35, bcrypt>=4.1
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg2
import streamlit as st

try:
    import bcrypt
    _BCRYPT_DISPONIBLE = True
except ImportError:
    _BCRYPT_DISPONIBLE = False


# ---------------------------------------------------------------------------
# Conexión BD
# ---------------------------------------------------------------------------

def _conn() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(st.secrets["DATABASE_URL"])
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError("DATABASE_URL no encontrada en secrets.toml") from exc


# ---------------------------------------------------------------------------
# Hash de contraseñas
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Genera hash bcrypt de una contraseña. Fallback simple si no hay bcrypt."""
    if _BCRYPT_DISPONIBLE:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    # Fallback (NO recomendado para producción real, solo para no romper
    # si bcrypt no está instalado — avisar al usuario en la UI)
    import hashlib
    return "sha256$" + hashlib.sha256(password.encode("utf-8")).hexdigest()


def _verificar_password(password: str, hash_guardado: str) -> bool:
    """Verifica una contraseña contra su hash guardado."""
    if hash_guardado.startswith("sha256$"):
        import hashlib
        return hash_guardado == "sha256$" + hashlib.sha256(
            password.encode("utf-8")
        ).hexdigest()
    if _BCRYPT_DISPONIBLE:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"), hash_guardado.encode("utf-8")
            )
        except (ValueError, TypeError):
            return False
    return False


# ---------------------------------------------------------------------------
# 1. MIGRACIÓN — tabla usuarios (idempotente)
# ---------------------------------------------------------------------------

def migrar_bd_usuarios() -> bool:
    """Crea la tabla 'usuarios' si no existe. Segura ejecutar siempre."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id_usuario      SERIAL PRIMARY KEY,
                username        TEXT UNIQUE NOT NULL,
                password_hash   TEXT NOT NULL,
                nombre_completo TEXT NOT NULL DEFAULT '',
                rol             TEXT NOT NULL DEFAULT 'INGENIERO',
                activo          BOOLEAN NOT NULL DEFAULT TRUE,
                fecha_creacion  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ultimo_acceso   TIMESTAMP
            )
        """)
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception as exc:
        st.session_state["_usuarios_error"] = str(exc)
        return False


def crear_usuario_inicial() -> None:
    """
    Siembra el usuario administrador inicial desde secrets.toml
    SOLO si la tabla usuarios está vacía. Idempotente y seguro
    de llamar en cada arranque.
    """
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute("SELECT COUNT(*) FROM usuarios")
        total = c.fetchone()[0]
        if total == 0:
            creds = st.secrets.get("credenciales", {})
            admin_user = creds.get("usuario", "admin")
            admin_pass = creds.get("password", "admin123")
            hash_pw    = _hash_password(admin_pass)
            c.execute(
                """INSERT INTO usuarios
                   (username, password_hash, nombre_completo, rol)
                   VALUES (%s, %s, %s, %s)""",
                (admin_user, hash_pw, "Administrador", "ADMIN"),
            )
            conn.commit()
        c.close()
        conn.close()
    except Exception:
        pass   # No bloquear el arranque si falla la siembra


# ---------------------------------------------------------------------------
# 2. AUTENTICACIÓN
# ---------------------------------------------------------------------------

def autenticar(username: str, password: str) -> dict | None:
    """
    Valida credenciales contra la BD.
    Retorna dict del usuario si es válido, None si no.
    Actualiza ultimo_acceso en caso de éxito.
    """
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT id_usuario, username, password_hash,
                      nombre_completo, rol, activo
               FROM usuarios WHERE username = %s""",
            (username.strip(),),
        )
        row = c.fetchone()
        if not row:
            c.close()
            conn.close()
            return None

        id_u, uname, pw_hash, nombre, rol, activo = row
        if not activo:
            c.close()
            conn.close()
            return None

        if _verificar_password(password, pw_hash):
            c.execute(
                "UPDATE usuarios SET ultimo_acceso = %s WHERE id_usuario = %s",
                (datetime.now(), id_u),
            )
            conn.commit()
            c.close()
            conn.close()
            return {
                "id":       id_u,
                "username": uname,
                "nombre":   nombre,
                "rol":      rol,
            }
        c.close()
        conn.close()
        return None
    except Exception as exc:
        st.error(f"Error de autenticación: {exc}")
        return None


# ---------------------------------------------------------------------------
# 3. GESTIÓN DE USUARIOS (alta, listado, desactivación)
# ---------------------------------------------------------------------------

def crear_usuario(
    username: str, password: str, nombre_completo: str, rol: str = "INGENIERO"
) -> tuple[bool, str]:
    """
    Crea un nuevo usuario. Retorna (éxito, mensaje).
    Roles válidos: ADMIN, INGENIERO, CONSULTOR.
    """
    if not username.strip() or not password.strip():
        return False, "Usuario y contraseña son obligatorios."
    if len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres."
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """INSERT INTO usuarios
               (username, password_hash, nombre_completo, rol)
               VALUES (%s, %s, %s, %s)""",
            (username.strip(), _hash_password(password),
             nombre_completo.strip(), rol),
        )
        conn.commit()
        c.close()
        conn.close()
        return True, f"Usuario '{username}' creado exitosamente."
    except psycopg2.errors.UniqueViolation:
        return False, f"El usuario '{username}' ya existe."
    except Exception as exc:
        return False, f"Error al crear usuario: {exc}"


def listar_usuarios() -> list[dict]:
    """Lista todos los usuarios para el panel de administración."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            """SELECT id_usuario, username, nombre_completo, rol,
                      activo, fecha_creacion, ultimo_acceso
               FROM usuarios ORDER BY fecha_creacion ASC"""
        )
        rows = c.fetchall()
        c.close()
        conn.close()
        return [
            {
                "id": r[0], "username": r[1], "nombre": r[2],
                "rol": r[3], "activo": r[4],
                "fecha_creacion": r[5], "ultimo_acceso": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


def cambiar_estado_usuario(id_usuario: int, activo: bool) -> bool:
    """Activa o desactiva un usuario (no se elimina, se desactiva)."""
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            "UPDATE usuarios SET activo = %s WHERE id_usuario = %s",
            (activo, id_usuario),
        )
        conn.commit()
        c.close()
        conn.close()
        return True
    except Exception:
        return False


def cambiar_password(id_usuario: int, nueva_password: str) -> tuple[bool, str]:
    """Cambia la contraseña de un usuario."""
    if len(nueva_password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres."
    try:
        conn = _conn()
        c    = conn.cursor()
        c.execute(
            "UPDATE usuarios SET password_hash = %s WHERE id_usuario = %s",
            (_hash_password(nueva_password), id_usuario),
        )
        conn.commit()
        c.close()
        conn.close()
        return True, "Contraseña actualizada."
    except Exception as exc:
        return False, f"Error: {exc}"


# ---------------------------------------------------------------------------
# RENDERIZADO — Login multi-usuario
# ---------------------------------------------------------------------------

def render_login() -> bool:
    """
    Pantalla de login con autenticación real contra BD multi-usuario.
    Retorna True si el usuario está autenticado.
    """
    if st.session_state.get("password_correct"):
        return True

    st.markdown("## 🔒 Hub de Automatización Ambiental")
    st.caption("Sistema multi-usuario con trazabilidad completa de acciones.")

    if not _BCRYPT_DISPONIBLE:
        st.warning(
            "⚠️ Librería `bcrypt` no instalada — usando hash de respaldo "
            "(menos seguro). Ejecuta: `pip install bcrypt`"
        )

    with st.form("login_form_v2"):
        username  = st.text_input("Usuario")
        password  = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Entrar", type="primary")

    if submitted:
        usuario = autenticar(username, password)
        if usuario:
            st.session_state["password_correct"] = True
            st.session_state["usuario_actual"]    = usuario["nombre"] or usuario["username"]
            st.session_state["usuario_id"]        = usuario["id"]
            st.session_state["usuario_username"]  = usuario["username"]
            st.session_state["usuario_rol"]       = usuario["rol"]
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos, o cuenta desactivada.")

    return False


# ---------------------------------------------------------------------------
# RENDERIZADO — Panel de administración de usuarios
# ---------------------------------------------------------------------------

def render_panel_usuarios() -> None:
    """
    Panel de administración de usuarios. Solo visible para rol ADMIN.
    Permite crear usuarios, activar/desactivar y cambiar contraseñas.
    """
    st.header("👥 Administración de Usuarios")

    if st.session_state.get("usuario_rol") != "ADMIN":
        st.error("⛔ Acceso restringido. Solo administradores pueden gestionar usuarios.")
        return

    st.caption(
        "Todos los usuarios pueden colaborar sobre cualquier proyecto. "
        "Este panel gestiona identidades para trazabilidad y auditoría."
    )

    # ── Crear nuevo usuario ─────────────────────────────────────────────────
    with st.expander("➕ Crear nuevo usuario", expanded=False):
        with st.form("form_nuevo_usuario"):
            col1, col2 = st.columns(2)
            with col1:
                n_username = st.text_input("Nombre de usuario (login)")
                n_password = st.text_input("Contraseña", type="password",
                                           help="Mínimo 6 caracteres")
            with col2:
                n_nombre = st.text_input("Nombre completo")
                n_rol    = st.selectbox("Rol", ["INGENIERO", "ADMIN", "CONSULTOR"])

            if st.form_submit_button("Crear usuario", type="primary"):
                ok, msg = crear_usuario(n_username, n_password, n_nombre, n_rol)
                if ok:
                    st.success(f"✅ {msg}")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    # ── Lista de usuarios ───────────────────────────────────────────────────
    st.subheader("Usuarios registrados")
    usuarios = listar_usuarios()

    if not usuarios:
        st.info("No hay usuarios registrados (esto no debería ocurrir si "
                 "ya iniciaste sesión).")
        return

    rol_icon = {"ADMIN": "👑", "INGENIERO": "🔧", "CONSULTOR": "👁️"}

    for u in usuarios:
        col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
        icono  = rol_icon.get(u["rol"], "👤")
        estado = "🟢 Activo" if u["activo"] else "🔴 Inactivo"

        with col1:
            st.markdown(f"{icono} **{u['nombre'] or u['username']}**  \n"
                       f"`@{u['username']}` · {u['rol']}")
        with col2:
            ultimo = u["ultimo_acceso"]
            ultimo_s = ultimo.strftime("%d/%m/%Y %H:%M") if ultimo else "Nunca"
            st.caption(f"{estado}\n\nÚltimo acceso: {ultimo_s}")
        with col3:
            if u["username"] != st.session_state.get("usuario_username"):
                nuevo_estado = not u["activo"]
                label = "Desactivar" if u["activo"] else "Activar"
                if st.button(label, key=f"toggle_{u['id']}"):
                    cambiar_estado_usuario(u["id"], nuevo_estado)
                    st.rerun()
            else:
                st.caption("*(tu cuenta)*")
        with col4:
            with st.popover("🔑 Cambiar contraseña"):
                nueva = st.text_input(
                    "Nueva contraseña", type="password",
                    key=f"newpass_{u['id']}",
                )
                if st.button("Actualizar", key=f"updpass_{u['id']}"):
                    ok, msg = cambiar_password(u["id"], nueva)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

        st.markdown('<hr style="margin:4px 0;border-color:#eee">',
                    unsafe_allow_html=True)
