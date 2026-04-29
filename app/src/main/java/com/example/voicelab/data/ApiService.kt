package com.example.voicelab.data

import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.POST

/**
 * Request data class for Standard TTS
 */
data class TtsRequest(
    val text: String,
    val voice: String
)

/**
 * Request data class for Voice Clone TTS
 */
data class CloneTtsRequest(
    val text: String,
    val speaker_id: String
)

/**
 * Retrofit API Service interface
 */
interface ApiService {
    @POST("tts")
    suspend fun generateTts(@Body request: TtsRequest): Response<ResponseBody>

    @POST("clone-tts")
    suspend fun generateCloneTts(@Body request: CloneTtsRequest): Response<ResponseBody>
}
