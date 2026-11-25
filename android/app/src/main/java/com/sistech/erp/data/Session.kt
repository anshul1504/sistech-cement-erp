package com.sistech.erp.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Holds the bearer token encrypted with an AES-GCM key that never leaves the Android Keystore.
 * The preferences file is excluded from backups and device transfer (res/xml/data_extraction_rules.xml).
 */
class Session(context: Context) {
    private val prefs = context.getSharedPreferences("sistech_session", Context.MODE_PRIVATE)

    var token: String?
        get() = prefs.getString("token", null)?.let { runCatching { decrypt(it) }.getOrNull() }
        set(value) {
            prefs.edit().apply { if (value == null) remove("token") else putString("token", encrypt(value)) }.apply()
        }

    var onSignedOut: (() -> Unit)? = null

    fun clear() {
        token = null
        onSignedOut?.invoke()
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build())
        }.generateKey()
    }

    private fun encrypt(plain: String): String {
        val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = c.iv + c.doFinal(plain.toByteArray())
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    private fun decrypt(stored: String): String {
        val bytes = Base64.decode(stored, Base64.NO_WRAP)
        val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12)) }
        return String(c.doFinal(bytes, 12, bytes.size - 12))
    }

    private companion object {
        const val ALIAS = "sistech_session_key"
        const val TRANSFORM = "AES/GCM/NoPadding"
    }
}
