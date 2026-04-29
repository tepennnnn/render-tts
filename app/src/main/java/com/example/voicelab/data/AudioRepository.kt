package com.example.voicelab.data

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

class AudioRepository(private val context: Context) {

    private var apiService: ApiService? = null
    private var currentBaseUrl: String? = null

    /**
     * Updates the Retrofit instance if the base URL changes.
     */
    private fun getApiService(baseUrl: String): ApiService {
        if (apiService == null || currentBaseUrl != baseUrl) {
            val logging = HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BODY
            }

            val client = OkHttpClient.Builder()
                .addInterceptor(logging)
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .build()

            val retrofit = Retrofit.Builder()
                .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()

            apiService = retrofit.create(ApiService::class.java)
            currentBaseUrl = baseUrl
        }
        return apiService!!
    }

    /**
     * Generates TTS audio and saves it to a cache file.
     */
    suspend fun generateTts(baseUrl: String, text: String, voice: String): Result<File> {
        return try {
            val service = getApiService(baseUrl)
            val response = service.generateTts(TtsRequest(text, voice))
            
            if (response.isSuccessful) {
                val body = response.body()
                if (body != null) {
                    val file = saveToCache(body.bytes())
                    Result.success(file)
                } else {
                    Result.failure(Exception("Empty response body"))
                }
            } else {
                Result.failure(Exception("API Error: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /**
     * Generates Clone TTS audio and saves it to a cache file.
     */
    suspend fun generateCloneTts(baseUrl: String, text: String, speakerId: String): Result<File> {
        return try {
            val service = getApiService(baseUrl)
            val response = service.generateCloneTts(CloneTtsRequest(text, speakerId))
            
            if (response.isSuccessful) {
                val body = response.body()
                if (body != null) {
                    val file = saveToCache(body.bytes())
                    Result.success(file)
                } else {
                    Result.failure(Exception("Empty response body"))
                }
            } else {
                Result.failure(Exception("API Error: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /**
     * Saves audio bytes to a temporary file in the cache directory.
     */
    private fun saveToCache(bytes: ByteArray): File {
        val cacheFile = File(context.cacheDir, "generated_audio.mp3")
        FileOutputStream(cacheFile).use { fos ->
            fos.write(bytes)
        }
        return cacheFile
    }

    /**
     * Downloads the audio file to the public Downloads folder.
     */
    fun downloadAudio(file: File): Result<String> {
        return try {
            val fileName = "TTS_${System.currentTimeMillis()}.mp3"
            val resolver = context.contentResolver

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val contentValues = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, fileName)
                    put(MediaStore.MediaColumns.MIME_TYPE, "audio/mpeg")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
                }
                val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, contentValues)
                uri?.let {
                    resolver.openOutputStream(it).use { outputStream ->
                        file.inputStream().use { inputStream ->
                            inputStream.copyTo(outputStream!!)
                        }
                    }
                    Result.success("File saved to Downloads")
                } ?: Result.failure(Exception("Failed to create MediaStore entry"))
            } else {
                val downloadsDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                val destFile = File(downloadsDir, fileName)
                file.copyTo(destFile, overwrite = true)
                Result.success("File saved to Downloads")
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
