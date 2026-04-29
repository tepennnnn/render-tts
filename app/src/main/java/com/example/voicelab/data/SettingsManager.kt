package com.example.voicelab.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

class SettingsManager(private val context: Context) {

    companion object {
        val BACKEND_URL_KEY = stringPreferencesKey("backend_url")
        const val DEFAULT_URL = "https://render-tts.onrender.com/"
    }

    val backendUrl: Flow<String> = context.dataStore.data
        .map { preferences ->
            preferences[BACKEND_URL_KEY] ?: DEFAULT_URL
        }

    suspend fun saveBackendUrl(url: String) {
        context.dataStore.edit { preferences ->
            preferences[BACKEND_URL_KEY] = url
        }
    }
}
