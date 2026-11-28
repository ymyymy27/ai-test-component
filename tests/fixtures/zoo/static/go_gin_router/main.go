package main

import "github.com/gin-gonic/gin"

func main() {
	r := gin.Default()
	api := r.Group("/api/v1")
	api.GET("/users", func(c *gin.Context) {})
	api.POST("/users", func(c *gin.Context) {})
	api.DELETE("/users/:id", func(c *gin.Context) {})
}
